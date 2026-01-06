import os
import uuid
import re
import requests
from flask import Flask, request, jsonify, send_from_directory
import google.generativeai as genai

app = Flask(__name__)

# Render 임시 저장 경로
STATIC_DIR = '/tmp/static'
if not os.path.exists(STATIC_DIR):
    os.makedirs(STATIC_DIR, exist_ok=True)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

def get_model():
    if not GEMINI_API_KEY: return None
    genai.configure(api_key=GEMINI_API_KEY)
    return genai.GenerativeModel('models/gemini-flash-latest')

def format_tel(tel_str):
    if not tel_str: return "없음"
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
        utterance = data.get('userRequest', {}).get('utterance', '').replace(" ", "")
        params = data.get('action', {}).get('params', {})
        client_extra = data.get('action', {}).get('clientExtra', {}) or {}

        # --- [모드 1: VCF 연락처 파일 생성] ---
        if "만들어줘" in utterance or "연락처" in utterance:
            name = client_extra.get('name', '이름없음')
            org = client_extra.get('org', '회사없음')
            tel = client_extra.get('tel', '')
            email = client_extra.get('email', '')

            vcf_content = f"BEGIN:VCARD\nVERSION:3.0\nFN:{name}\nORG:{org}\nTEL:{tel}\nEMAIL:{email}\nEND:VCARD"
            file_name = f"biz_{uuid.uuid4().hex[:8]}.vcf"
            with open(os.path.join(STATIC_DIR, file_name), "w", encoding="utf-8") as f:
                f.write(vcf_content)

            download_url = f"{request.host_url.rstrip('/')}/download/{file_name}"
            return jsonify({
                "version": "2.0",
                "template": {
                    "outputs": [{
                        "basicCard": {
                            "title": f"{name}님의 연락처 생성 완료",
                            "description": "아래 버튼을 눌러 파일을 다운로드하세요.",
                            "buttons": [{"action": "webLink", "label": "📥 VCF 다운로드", "webLinkUrl": download_url}]
                        }
                    }]
                }
            })

        # --- [모드 2: 정보 추출] ---
        image_url = params.get('image')
        # AI에게 형식을 더 엄격하게 지시 (추출 실패 방지)
        prompt = """명함이나 사업자등록증에서 정보를 추출해줘. 
결과는 반드시 아래 형식을 지켜줘:
상호:내용
대표:내용
주소:내용
전화:내용
팩스:내용
이메일:내용
정보가 없으면 '없음'이라고 적어줘."""

        if image_url:
            img_res = requests.get(image_url, timeout=5)
            response = model.generate_content([prompt, {'mime_type': 'image/jpeg', 'data': img_res.content}])
        else:
            response = model.generate_content(f"{prompt}\n\n텍스트 내용:\n{data.get('userRequest', {}).get('utterance', '')}")

        res_text = response.text.strip()
        info = {"상호": "없음", "대표": "없음", "주소": "없음", "전화": "없음", "팩스": "없음", "이메일": "없음"}
        
        # 파싱 로직 강화
        for line in res_text.splitlines():
            line = line.replace('*', '').strip()
            if ':' in line:
                key, val = line.split(':', 1)
                key = key.strip()
                val = val.strip()
                for k in info.keys():
                    if k in key:
                        if key in ['전화', '팩스']: val = format_tel(val)
                        info[k] = val

        result_display = f"📋 분석 결과:\n\n상호 : {info['상호']}\n대표 : {info['대표']}\n주소 : {info['주소']}\n전화 : {info['전화']}\n팩스 : {info['팩스']}\n이메일 : {info['이메일']}"

        return jsonify({
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": result_display}}],
                "quickReplies": [{
                    "label": "📁 연락처 파일 만들기",
                    "action": "message",
                    "messageText": "연락처 파일 만들어줘",
                    "extra": {"name": info['대표'], "org": info['상호'], "tel": info['전화'], "email": info['이메일']}
                }]
            }
        })

    except Exception as e:
        return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": f"🚨 오류: {str(e)[:40]}"}}]}})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
