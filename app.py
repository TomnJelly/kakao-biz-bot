import os
import uuid
import re
import requests
from flask import Flask, request, jsonify, send_from_directory
import google.generativeai as genai

app = Flask(__name__)

# 임시 파일 저장 경로 (Render 환경용)
STATIC_DIR = '/tmp/static'
os.makedirs(STATIC_DIR, exist_ok=True)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

def get_model():
    if not GEMINI_API_KEY: return None
    genai.configure(api_key=GEMINI_API_KEY)
    # 현재 환경에서 작동하는 최신 모델 (latest) 유지
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
def health_check():
    return "OK", 200

@app.route('/download/<filename>')
def download_file(filename):
    return send_from_directory(STATIC_DIR, filename, as_attachment=True)

@app.route('/api/get_biz_info', methods=['POST'])
@app.route('/api/get_biz_info/', methods=['POST'])
def get_biz_info():
    try:
        model = get_model()
        data = request.get_json(force=True)
        
        # [데이터 추출] 잘 됐던 방식 그대로 utterance에서 가져옵니다.
        user_input = data.get('userRequest', {}).get('utterance', '') 
        
        params = data.get('action', {}).get('params', {})
        client_extra = data.get('action', {}).get('clientExtra', {}) or {}

        # --- [모드 1] VCF 연락처 파일 생성 ---
        # 사용자가 "연락처 파일 만들어줘" 버튼을 눌렀을 때 작동
        if "연락처" in user_input.replace(" ", "") or client_extra:
            name = client_extra.get('name') or "이름없음"
            org = client_extra.get('org') or ""
            tel = client_extra.get('tel') or ""
            email = client_extra.get('email') or ""
            addr = client_extra.get('addr') or "" # 주소 데이터 확보

            # [요청사항 1] 이름 형식을 "이름(상호)"로 변경
            display_name = f"{name}({org})" if org and org != "없음" else name
            
            # [요청사항 2] VCF에 주소(ADR) 필드 추가 및 한글 인코딩
            vcf_content = (
                "BEGIN:VCARD\n"
                "VERSION:3.0\n"
                f"FN;CHARSET=UTF-8:{display_name}\n"
                f"N;CHARSET=UTF-8:{display_name};;;;\n"
                f"ORG;CHARSET=UTF-8:{org}\n"
                f"TEL;TYPE=CELL:{tel}\n"
                f"EMAIL;TYPE=INTERNET:{email}\n"
                f"ADR;CHARSET=UTF-8:;;{addr};;;\n" # '주소' 필드에 정확히 삽입
                "END:VCARD"
            )
            
            file_name = f"biz_{uuid.uuid4().hex[:8]}.vcf"
            file_path = os.path.join(STATIC_DIR, file_name)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(vcf_content)

            download_url = f"{request.host_url.rstrip('/')}/download/{file_name}"
            return jsonify({
                "version": "2.0",
                "template": {
                    "outputs": [{
                        "basicCard": {
                            "title": f"📂 {display_name} 연락처",
                            "description": f"상호: {org}\n전화: {tel}\n주소: {addr}", # 카드 설명에 주소 표시
                            "buttons": [{"action": "webLink", "label": "VCF 파일 저장", "webLinkUrl": download_url}]
                        }
                    }]
                }
            })

        # --- [모드 2] 명함/이미지 분석 ---
        image_url = params.get('image') or params.get('sys_plugin_image')
        
        prompt = """명함에서 정보를 추출해. 반드시 다음 형식을 지켜:
상호:내용
대표:내용
주소:내용
전화:내용
팩스:내용
이메일:내용
정보 없으면 '없음'으로 표시해."""

        if image_url:
            img_res = requests.get(image_url, timeout=5)
            response = model.generate_content([prompt, {"mime_type": "image/jpeg", "data": img_res.content}])
        else:
            response = model.generate_content(f"{prompt}\n\n텍스트 내용:\n{user_input}")

        res_text = response.text.strip()
        info = {"상호": "없음", "대표": "없음", "주소": "없음", "전화": "없음", "팩스": "없음", "이메일": "없음"}
        
        for line in res_text.splitlines():
            if ':' in line:
                k, v = line.split(':', 1)
                k = k.replace('*', '').strip()
                v = v.strip()
                for key in info:
                    if key in k:
                        if key in ['전화', '팩스']: v = format_tel(v)
                        info[key] = v

        return jsonify({
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": f"📋 분석 결과\n\n상호: {info['상호']}\n대표: {info['대표']}\n주소: {info['주소']}\n전화: {info['전화']}\n팩스: {info['팩스']}\n이메일: {info['이메일']}"}}],
                "quickReplies": [{
                    "label": "📁 연락처 파일 만들기",
                    "action": "message",
                    "messageText": "연락처 파일 만들어줘",
                    "extra": {
                        "name": info['대표'], 
                        "org": info['상호'], 
                        "tel": info['전화'], 
                        "email": info['이메일'], 
                        "addr": info['주소'] # 퀵리플라이 데이터에도 주소 포함
                    }
                }]
            }
        })

    except Exception as e:
        return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."}}]}})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
