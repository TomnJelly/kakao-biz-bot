import os
import re
import requests
from flask import Flask, request, jsonify
import google.generativeai as genai

app = Flask(__name__)

# Render 환경 변수 설정
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

def get_model():
    if not GEMINI_API_KEY: return None
    genai.configure(api_key=GEMINI_API_KEY)
    return genai.GenerativeModel('models/gemini-1.5-flash')

def format_tel(tel_str):
    if not tel_str: return ""
    nums = re.sub(r'[^0-9]', '', tel_str)
    if len(nums) == 9: return f"{nums[0:2]}-{nums[2:5]}-{nums[5:]}"
    elif len(nums) == 10:
        if nums.startswith('02'): return f"{nums[0:2]}-{nums[2:6]}-{nums[6:]}"
        else: return f"{nums[0:3]}-{nums[3:6]}-{nums[6:]}"
    elif len(nums) == 11: return f"{nums[0:3]}-{nums[3:7]}-{nums[7:]}"
    return tel_str

@app.route('/api/get_biz_info', methods=['POST'])
def get_biz_info():
    try:
        model = get_model()
        data = request.get_json(force=True)
        utterance = data.get('userRequest', {}).get('utterance', '').replace(" ", "")
        params = data.get('action', {}).get('params', {})
        client_extra = data.get('action', {}).get('clientExtra', {}) or {}

        # --- [모드 1: 연락처 정보 텍스트 제공] ---
        if "만들어줘" in utterance:
            name = client_extra.get('name', '이름없음')
            org = client_extra.get('org', '회사없음')
            tel = client_extra.get('tel', '번호없음')
            email = client_extra.get('email', '이메일없음')
            
            vcf_info = f"📇 [연락처 정보]\n\n👤 이름: {name}\n🏢 회사: {org}\n📞 전화: {tel}\n📧 이메일: {email}\n\n위 내용을 복사해서 주소록에 저장하세요!"
            return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": vcf_info}}]}})

        # --- [모드 2: 정보 추출] ---
        image_url = params.get('image')
        prompt = "사업자등록증에서 상호, 대표, 주소, 전화, 팩스, 이메일을 찾아서 '항목 : 내용' 형식으로만 한 줄씩 적어줘. 다른 말은 하지마."

        if image_url:
            img_res = requests.get(image_url, timeout=10)
            response = model.generate_content([prompt, {'mime_type': 'image/jpeg', 'data': img_res.content}])
        else:
            return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "분석할 사진을 먼저 보내주세요! 📸"}}]}})

        res_text = response.text.strip()
        info = {}
        for line in res_text.splitlines():
            if ':' in line:
                k, v = line.split(':', 1)
                key, val = k.strip().replace('*', ''), v.strip()
                if key in ['전화', '팩스']: val = format_tel(val)
                info[key] = val

        result_display = f"📋 분석 결과:\n\n"
        result_display += f"상호 : {info.get('상호', '없음')}\n"
        result_display += f"대표 : {info.get('대표', '없음')}\n"
        result_display += f"주소 : {info.get('주소', '없음')}\n"
        result_display += f"전화 : {info.get('전화', '없음')}\n"
        result_display += f"팩스 : {info.get('팩스', '없음')}\n"
        result_display += f"이메일 : {info.get('이메일', '없음')}"

        return jsonify({
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": result_display}}],
                "quickReplies": [{
                    "label": "📁 연락처 정보 보기",
                    "action": "message",
                    "messageText": "연락처 파일 만들어줘",
                    "extra": {
                        "name": info.get('대표', ''), "org": info.get('상호', ''),
                        "tel": info.get('전화', ''), "email": info.get('이메일', '')
                    }
                }]
            }
        })
    except Exception as e:
        return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": f"🚨 확인 중: {str(e)[:40]}"}}]}})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
