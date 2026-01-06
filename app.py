import os
import uuid
import re
import requests
from flask import Flask, request, jsonify
import google.generativeai as genai

app = Flask(__name__)

# Render의 임시 디렉토리 설정
STATIC_DIR = '/tmp/static'
if not os.path.exists(STATIC_DIR):
    os.makedirs(STATIC_DIR, exist_ok=True)

# Render 환경 변수에서 API 키 가져오기
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

def get_model():
    if not GEMINI_API_KEY: return None
    genai.configure(api_key=GEMINI_API_KEY)
    return genai.GenerativeModel('models/gemini-flash-latest')

def format_tel(tel_str):
    if not tel_str: return ""
    nums = re.sub(r'[^0-9]', '', tel_str)
    length = len(nums)
    if length == 9: return f"{nums[0:2]}-{nums[2:5]}-{nums[5:]}"
    elif length == 10:
        if nums.startswith('02'): return f"{nums[0:2]}-{nums[2:6]}-{nums[6:]}"
        else: return f"{nums[0:3]}-{nums[3:6]}-{nums[6:]}"
    elif length == 11: return f"{nums[0:3]}-{nums[3:7]}-{nums[7:]}"
    return tel_str

@app.route('/api/get_biz_info', methods=['POST'])
def get_biz_info():
    try:
        model = get_model()
        data = request.get_json(force=True)
        params = data.get('action', {}).get('params', {})
        utterance = data.get('userRequest', {}).get('utterance', '')
        client_extra = data.get('action', {}).get('clientExtra', {}) or {}

        # --- [모드 1: VCF 파일 생성] ---
        if "만들어줘" in utterance.replace(" ", ""):
            name = client_extra.get('name', '이름없음').strip()
            org = client_extra.get('org', '').strip()
            vcf_text = f"BEGIN:VCARD\nVERSION:3.0\nFN:{name}\nORG:{org}\n"
            if client_extra.get('tel'): vcf_text += f"TEL:{client_extra['tel']}\n"
            if client_extra.get('email'): vcf_text += f"EMAIL:{client_extra['email']}\n"
            vcf_text += "END:VCARD"
            
            file_name = f"biz_{uuid.uuid4().hex[:8]}.vcf"
            # Render 임시 폴더에 저장
            with open(os.path.join(STATIC_DIR, file_name), "w", encoding="utf-8") as f:
                f.write(vcf_text)
            
            # 주의: Render 무료 티어는 정적 파일 영구 저장이 안 되므로 결과 텍스트 위주로 활용
            return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": f"✅ {name}님의 연락처 정보가 준비되었습니다."}}]}})

        # --- [모드 2: 정보 추출] ---
        image_url = params.get('image')
        user_input = params.get('user_input', utterance)
        prompt = "사업자 정보(상호, 대표, 주소, 전화, 팩스, 이메일)를 '항목 : 내용' 형식으로 추출해줘."

        if image_url:
            img_res = requests.get(image_url, timeout=10) # Render는 외부 접속 허용
            response = model.generate_content([prompt, {'mime_type': 'image/jpeg', 'data': img_res.content}])
        else:
            response = model.generate_content(f"{prompt}\n내용:\n{user_input}")

        res_text = response.text.strip()
        info = {}
        cleaned_lines = []
        for line in res_text.splitlines():
            if ':' in line:
                k, v = line.split(':', 1)
                key, val = k.strip().replace('*', ''), v.strip().rstrip('.')
                if key in ['전화', '팩스']: val = format_tel(val)
                info[key] = val
                cleaned_lines.append(f"{key} : {val}")

        return jsonify({
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": "📋 분석 결과:\n\n" + "\n".join(cleaned_lines)}}],
                "quickReplies": [{
                    "label": "📁 연락처 파일 만들기",
                    "action": "message",
                    "messageText": "연락처 파일 만들어줘",
                    "extra": {
                        "name": info.get('대표', ''), "org": info.get('상호', ''),
                        "tel": info.get('전화', ''), "email": info.get('이메일', ''), "addr": info.get('주소', '')
                    }
                }]
            }
        })
    except Exception as e:
        return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": f"🚨 오류: {str(e)[:40]}"}}]}})

if __name__ == '__main__':
    # Render는 PORT 환경 변수를 사용함
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
