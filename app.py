import os
import uuid
import re
import requests
from flask import Flask, request, jsonify, send_from_directory
import google.generativeai as genai

app = Flask(__name__)

# 임시 파일 저장 경로
STATIC_DIR = '/tmp/static'
if not os.path.exists(STATIC_DIR):
    os.makedirs(STATIC_DIR, exist_ok=True)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

def get_model():
    if not GEMINI_API_KEY: return None
    genai.configure(api_key=GEMINI_API_KEY)
    return genai.GenerativeModel('models/gemini-flash-latest')

def format_tel(tel_str):
    if not tel_str or "없음" in tel_str: return ""
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
        user_input = data.get('userRequest', {}).get('utterance', '').replace(" ", "")
        params = data.get('action', {}).get('params', {})
        client_extra = data.get('action', {}).get('clientExtra', {}) or {}

        # =====================================
        # [모드 1] VCF 연락처 파일 생성
        # =====================================
        if "연락처" in user_input or client_extra:
            raw_name = str(client_extra.get('name', '')).strip()
            org = str(client_extra.get('org', '')).strip()
            tel = client_extra.get('tel', '')
            email = client_extra.get('email', '')
            addr = client_extra.get('addr', '')
            fax = client_extra.get('fax', '')

            # [이름 최적화] 대표자명(상호)
            has_name = raw_name and raw_name != '없음'
            has_org = org and org != '없음'

            if has_name and has_org:
                display_name = f"{raw_name}({org})"
            elif has_name:
                display_name = raw_name
            elif has_org:
                display_name = org
            else:
                display_name = "신규연락처"

            # VCF 생성 (주소 필드 TYPE 제거하여 호환성 강화)
            vcf_lines = [
                "BEGIN:VCARD",
                "VERSION:3.0",
                f"FN;CHARSET=UTF-8:{display_name}",
                f"N;CHARSET=UTF-8:{display_name};;;;",
                f"ORG;CHARSET=UTF-8:{org if has_org else ''}",
                f"TEL;TYPE=CELL:{tel}",
                f"TEL;TYPE=FAX:{fax}",
                f"EMAIL;TYPE=INTERNET:{email}",
                # ADR 필드에서 WORK를 제거하고 일반 주소로 설정
                f"ADR;CHARSET=UTF-8:;;{addr};;;", 
                "END:VCARD"
            ]
            vcf_content = "\n".join(vcf_lines)
            
            file_name = f"biz_{uuid.uuid4().hex[:8]}.vcf"
            file_path = os.path.join(STATIC_DIR, file_name)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(vcf_content)

            download_url = f"{request.host_url.rstrip('/')}/download/{file_name}"
            
            return jsonify({
                "version": "2.0",
                "template": {
                    "outputs": [{
                        "simpleText": {
                            "text": f"✅ 연락처 생성이 완료되었습니다.\n\n👤 저장명: {display_name}\n\n🔗 아래 링크를 클릭해 저장하세요:\n{download_url}"
                        }
                    }]
                }
            })

        # =====================================
        # [모드 2] 명함 정보 분석 (이전과 동일)
        # =====================================
        image_url = params.get('image') or params.get('sys_plugin_image')
        prompt = "명함에서 상호, 대표, 주소, 전화, 팩스, 이메일을 추출해줘. 항목별로 '항목:내용' 형식을 지켜줘. 없으면 '없음'으로 적어."

        if image_url:
            img_res = requests.get(image_url, timeout=5)
            response = model.generate_content([prompt, {"mime_type": "image/jpeg", "data": img_res.content}])
        else:
            response = model.generate_content(f"{prompt}\n\n내용:\n{data.get('userRequest', {}).get('utterance', '')}")

        res_text = response.text.strip()
        info = {"상호": "없음", "대표": "없음", "주소": "없음", "전화": "없음", "팩스": "없음", "이메일": "없음"}
        
        for line in res_text.splitlines():
            if ':' in line:
                k, v = line.split(':', 1)
                k, v = k.replace('*', '').strip(), v.strip()
                for key in info:
                    if key in k:
                        if key in ['전화', '팩스']: v = format_tel(v)
                        info[key] = v

        result_display = f"📋 분석 결과\n\n상호: {info['상호']}\n대표: {info['대표']}\n주소: {info['주소']}\n전화: {info['전화']}\n팩스: {info['팩스']}\n이메일: {info['이메일']}"

        return jsonify({
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": result_display}}],
                "quickReplies": [{
                    "label": "📁 연락처 파일 만들기",
                    "action": "message",
                    "messageText": "연락처 파일 만들어줘",
                    "extra": {
                        "name": info['대표'], "org": info['상호'], 
                        "tel": info['전화'], "email": info['이메일'],
                        "addr": info['주소'], "fax": info['팩스']
                    }
                }]
            }
        })

    except Exception as e:
        return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": f"🚨 오류: {str(e)[:40]}"}}]}})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
