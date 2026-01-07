import os
import uuid
import re
import requests
from flask import Flask, request, jsonify, send_from_directory
import google.generativeai as genai

app = Flask(__name__)

# 임시 파일 저장 경로
STATIC_DIR = '/tmp/static'
os.makedirs(STATIC_DIR, exist_ok=True)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

def get_model():
    if not GEMINI_API_KEY: return None
    genai.configure(api_key=GEMINI_API_KEY)
    return genai.GenerativeModel('models/gemini-flash-latest')

def format_tel(tel_str):
    if not tel_str or "없음" in tel_str: return "없음"
    nums = re.sub(r'[^0-9]', '', tel_str)
    if len(nums) == 9: return f"{nums[0:2]}-{nums[2:5]}-{nums[5:]}"
    elif len(nums) == 10:
        if nums.startswith('02'): return f"{nums[0:2]}-{nums[2:6]}-{nums[6:]}"
        else: return f"{nums[0:3]}-{nums[3:6]}-{nums[6:]}"
    elif len(nums) == 11: return f"{nums[0:3]}-{nums[3:7]}-{nums[7:]}"
    return tel_str

@app.route('/')
def health_check(): return "OK", 200

@app.route('/download/<filename>')
def download_file(filename):
    return send_from_directory(STATIC_DIR, filename, as_attachment=True)

@app.route('/api/get_biz_info', methods=['POST'])
@app.route('/api/get_biz_info/', methods=['POST'])
def get_biz_info():
    try:
        model = get_model()
        data = request.get_json(force=True)
        
        params = data.get('action', {}).get('params', {})
        # 사용자님의 user_input 설정과 utterance 교차 확인
        user_text = params.get('user_input') or data.get('userRequest', {}).get('utterance', '')
        client_extra = data.get('action', {}).get('clientExtra', {}) or {}

        # --- [모드 1] VCF 연락처 생성 및 '텍스트 링크' 발송 ---
        if "연락처" in user_text.replace(" ", "") or client_extra:
            name = client_extra.get('name') or "이름없음"
            org = client_extra.get('org', "").strip('.') or "" # 상호 끝 마침표 제거
            tel = client_extra.get('tel') or ""
            email = client_extra.get('email') or ""
            addr = client_extra.get('addr') or ""

            display_name = f"{name}({org})" if org and org != "없음" else name
            
            vcf_content = (
                "BEGIN:VCARD\n"
                "VERSION:3.0\n"
                f"FN;CHARSET=UTF-8:{display_name}\n"
                f"N;CHARSET=UTF-8:{display_name};;;;\n"
                f"ORG;CHARSET=UTF-8:{org}\n"
                f"TEL;TYPE=CELL:{tel}\n"
                f"EMAIL;TYPE=INTERNET:{email}\n"
                f"ADR;CHARSET=UTF-8:;;{addr};;;\n"
                "END:VCARD"
            )
            
            file_name = f"biz_{uuid.uuid4().hex[:8]}.vcf"
            file_path = os.path.join(STATIC_DIR, file_name)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(vcf_content)

            download_url = f"{request.host_url.rstrip('/')}/download/{file_name}"
            
            # 버튼 없이 텍스트 링크로만 응답
            return jsonify({
                "version": "2.0",
                "template": {
                    "outputs": [{
                        "simpleText": {
                            "text": f"📂 {display_name} 연락처 생성이 완료되었습니다.\n\n아래 링크를 클릭하여 파일을 저장하세요:\n{download_url}"
                        }
                    }]
                }
            })

        # --- [모드 2] 명함 분석 ---
        image_url = params.get('image') or params.get('sys_plugin_image')
        prompt = "명함에서 상호, 대표, 주소, 전화, 팩스, 이메일을 추출해. '항목:내용' 형식으로 쓰고 없으면 '없음'으로 표시해."

        if image_url:
            img_res = requests.get(image_url, timeout=5)
            response = model.generate_content([prompt, {"mime_type": "image/jpeg", "data": img_res.content}])
        else:
            if not user_text.strip():
                 return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "분석할 내용을 입력해주세요."}}]}})
            response = model.generate_content(f"{prompt}\n\n내용:\n{user_text}")

        res_text = response.text.strip()
        info = {"상호": "없음", "대표": "없음", "주소": "없음", "전화": "없음", "팩스": "없음", "이메일": "없음"}
        
        for line in res_text.splitlines():
            line = re.sub(r'[*#\-]', '', line).strip()
            if ':' in line:
                k, v = line.split(':', 1)
                for key in info:
                    if key in k:
                        val = v.strip().strip('.') # 상호 등 끝에 붙은 마침표 제거
                        info[key] = format_tel(val) if key in ['전화', '팩스'] else val

        return jsonify({
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": f"📋 분석 결과\n\n상호: {info['상호']}\n대표: {info['대표']}\n주소: {info['주소']}\n전화: {info['전화']}\n팩스: {info['팩스']}\n이메일: {info['이메일']}"}}],
                "quickReplies": [{
                    "label": "📁 연락처 파일 만들기",
                    "action": "message",
                    "messageText": "연락처 파일 만들어줘",
                    "extra": {"name": info['대표'], "org": info['상호'], "tel": info['전화'], "email": info['이메일'], "addr": info['주소']}
                }]
            }
        })

    except Exception as e:
        return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "처리에 실패했습니다. 다시 시도해주세요."}}]}})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
