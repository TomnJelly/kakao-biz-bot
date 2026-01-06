import os
import uuid
import re
import requests
from flask import Flask, request, jsonify, send_from_directory
import google.generativeai as genai

app = Flask(__name__)

# Render의 임시 저장 경로 설정 (파일 다운로드용)
STATIC_DIR = '/tmp/static'
if not os.path.exists(STATIC_DIR):
    os.makedirs(STATIC_DIR, exist_ok=True)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

def get_model():
    if not GEMINI_API_KEY: return None
    genai.configure(api_key=GEMINI_API_KEY)
    return genai.GenerativeModel('models/gemini-flash-latest')

def format_tel(tel_str):
    if not tel_str: return ""
    nums = re.sub(r'[^0-9]', '', tel_str)
    if len(nums) == 9: return f"{nums[0:2]}-{nums[2:5]}-{nums[5:]}"
    elif len(nums) == 10:
        if nums.startswith('02'): return f"{nums[0:2]}-{nums[2:6]}-{nums[6:]}"
        else: return f"{nums[0:3]}-{nums[3:6]}-{nums[6:]}"
    elif len(nums) == 11: return f"{nums[0:3]}-{nums[3:7]}-{nums[7:]}"
    return tel_str

# 1. 상태 확인용 루팅
@app.route('/')
def health_check():
    return "Bot is alive!", 200

# 2. VCF 파일 다운로드 경로
@app.route('/download/<filename>')
def download_file(filename):
    return send_from_directory(STATIC_DIR, filename, as_attachment=True)

@app.route('/api/get_biz_info', methods=['POST'])
def get_biz_info():
    try:
        model = get_model()
        data = request.get_json(force=True)
        utterance = data.get('userRequest', {}).get('utterance', '').replace(" ", "")
        params = data.get('action', {}).get('params', {})
        client_extra = data.get('action', {}).get('clientExtra', {}) or {}

        # --- [연락처 파일 생성 로직] ---
        if "연락처" in utterance or "만들어줘" in utterance:
            name = client_extra.get('name', '이름없음').strip()
            org = client_extra.get('org', '').strip()
            tel = client_extra.get('tel', '').strip()
            email = client_extra.get('email', '').strip()

            vcf_content = f"BEGIN:VCARD\nVERSION:3.0\nFN:{name}\nORG:{org}\nTEL:{tel}\nEMAIL:{email}\nEND:VCARD"
            file_name = f"contact_{uuid.uuid4().hex[:8]}.vcf"
            file_path = os.path.join(STATIC_DIR, file_name)

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(vcf_content)

            # Render 서비스 URL 가져오기 (예: https://kakao-biz-bot.onrender.com)
            host_url = request.host_url.rstrip('/')
            download_url = f"{host_url}/download/{file_name}"

            return jsonify({
                "version": "2.0",
                "template": {
                    "outputs": [{
                        "basicCard": {
                            "title": f"{name}님의 연락처 파일",
                            "description": "아래 버튼을 눌러 연락처를 저장하세요.",
                            "buttons": [{
                                "action": "webLink",
                                "label": "📥 VCF 파일 다운로드",
                                "webLinkUrl": download_url
                            }]
                        }
                    }]
                }
            })

        # --- [AI 정보 추출 로직] ---
        image_url = params.get('image')
        prompt = "사업자 정보(상호, 대표, 주소, 전화, 팩스, 이메일)를 '항목 : 내용' 형식으로 한 줄씩 적어줘. 없는 정보는 '없음' 적기."

        if image_url:
            img_res = requests.get(image_url, timeout=5) # 5초 타임아웃 방지 위해 짧게 설정
            response = model.generate_content([prompt, {'mime_type': 'image/jpeg', 'data': img_res.content}])
        else:
            response = model.generate_content(f"{prompt}\n\n내용:\n{data.get('userRequest', {}).get('utterance', '')}")

        res_text = response.text.strip()
        info = {}
        for line in res_text.splitlines():
            if ':' in line:
                k, v = line.split(':', 1)
                key, val = k.strip().replace('*', ''), v.strip()
                if key in ['전화', '팩스']: val = format_tel(val)
                info[key] = val

        result_display = f"📋 분석 결과:\n\n상호 : {info.get('상호', '없음')}\n대표 : {info.get('대표', '없음')}\n주소 : {info.get('주소', '없음')}\n전화 : {info.get('전화', '없음')}\n팩스 : {info.get('팩스', '없음')}\n이메일 : {info.get('이메일', '없음')}"

        return jsonify({
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": result_display}}],
                "quickReplies": [{
                    "label": "📁 연락처 파일 만들기",
                    "action": "message",
                    "messageText": "연락처 파일 만들어줘",
                    "extra": {
                        "name": info.get('대표', ''),
                        "org": info.get('상호', ''),
                        "tel": info.get('전화', ''),
                        "email": info.get('이메일', '')
                    }
                }]
            }
        })

    except Exception as e:
        return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": f"🚨 오류: {str(e)[:30]}"}}]}})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
