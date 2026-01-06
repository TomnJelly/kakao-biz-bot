import os
import re
import requests
from flask import Flask, request, jsonify
import google.generativeai as genai

app = Flask(__name__)

# 1. 환경 변수 설정
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

def get_model():
    if not GEMINI_API_KEY:
        return None
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

# --- [추가된 부분] Render 상태 체크 및 서버 접속 확인용 ---
@app.route('/')
def health_check():
    return "Kakao Biz Bot is Running!", 200
# --------------------------------------------------

@app.route('/api/get_biz_info', methods=['POST'])
def get_biz_info():
    try:
        model = get_model()
        if not model:
            return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "API 키가 설정되지 않았습니다."}}]}})

        data = request.get_json(force=True)
        
        # 사용자가 현재 머물고 있는 '블록 이름' 가져오기
        block_name = data.get('userRequest', {}).get('block', {}).get('name', '')
        utterance = data.get('userRequest', {}).get('utterance', '').strip()
        params = data.get('action', {}).get('params', {})
        client_extra = data.get('action', {}).get('clientExtra', {}) or {}

        # 1. 연락처 생성 로직 (버튼 클릭 시)
        if "연락처" in block_name or "만들어줘" in utterance.replace(" ", ""):
            vcf_info = (f"📇 [연락처 정보]\n\n"
                        f"👤 이름: {client_extra.get('name', '이름없음')}\n"
                        f"🏢 회사: {client_extra.get('org', '회사없음')}\n"
                        f"📞 전화: {client_extra.get('tel', '번호없음')}\n"
                        f"📧 이메일: {client_extra.get('email', '이메일없음')}")
            return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": vcf_info}}]}})

        # 2. 정보 추출 로직
        prompt = "사업자 정보(상호, 대표, 주소, 전화, 팩스, 이메일)를 '항목 : 내용' 형식으로 한 줄씩 적어줘. 없는 정보는 '없음' 적기."

        # 사진 입력 대응
        if "사진" in block_name:
            image_url = params.get('image')
            if not image_url:
                return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "사진을 찾을 수 없습니다."}}]}})
            
            img_res = requests.get(image_url, timeout=10)
            response = model.generate_content([prompt, {'mime_type': 'image/jpeg', 'data': img_res.content}])
        
        # 텍스트 입력 대응
        elif "텍스트" in block_name or utterance:
            response = model.generate_content(f"{prompt}\n\n내용:\n{utterance}")
        
        else:
            return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "분석할 내용을 입력해주세요."}}]}})

        # AI 결과 처리
        res_text = response.text.strip()
        info = {}
        for line in res_text.splitlines():
            if ':' in line:
                k, v = line.split(':', 1)
                key, val = k.strip().replace('*', ''), v.strip()
                if key in ['전화', '팩스']: val = format_tel(val)
                info[key] = val

        result_display = (f"📋 분석 결과:\n\n"
                          f"상호 : {info.get('상호', '없음')}\n"
                          f"대표 : {info.get('대표', '없음')}\n"
                          f"주소 : {info.get('주소', '없음')}\n"
                          f"전화 : {info.get('전화', '없음')}\n"
                          f"팩스 : {info.get('팩스', '없음')}\n"
                          f"이메일 : {info.get('이메일', '없음')}")

        return jsonify({
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": result_display}}],
                "quickReplies": [{
                    "label": "📁 연락처 정보 보기",
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
        return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": f"🚨 오류 발생: {str(e)[:50]}"}}]}})

if __name__ == '__main__':
    # Render 환경의 PORT 변수를 읽어오고, 없으면 10000 사용
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
