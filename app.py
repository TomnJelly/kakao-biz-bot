import os
import uuid
import re
import requests
import threading
import json
import time  # 🚀 시간 체크용 추가
from datetime import datetime  # 🚀 날짜 체크용 추가
from flask import Flask, request, jsonify, send_from_directory
from google import genai
from google.genai import types

app = Flask(__name__)

# 경로 설정 (성공했던 /tmp/static 유지)
STATIC_DIR = '/tmp/static'
os.makedirs(STATIC_DIR, exist_ok=True)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# 🚀 [수정] 모델별 독립 쿼터 관리 설정
call_count = 0
MODELS = ['gemini-3-flash-preview', 'gemini-2.5-flash', 'gemini-2.5-flash-lite']
model_usage = {model: {'day': '', 'day_count': 0, 'last_calls': []} for model in MODELS}

# 🚀 [추가] 서버 자체 깨우기 (Self-Ping) 로직
def keep_alive():
    time.sleep(30)
    while True:
        try:
            # Render 환경변수 사용
            url = os.environ.get("RENDER_EXTERNAL_URL")
            if url:
                requests.get(url, timeout=10)
        except:
            pass
        time.sleep(600) # 10분마다 실행

threading.Thread(target=keep_alive, daemon=True).start()

def get_client():
    if not GEMINI_API_KEY: return None
    return genai.Client(api_key=GEMINI_API_KEY)

# 🚀 [추가] 모델별 쿼터 체크 함수 (1분 3회 / 하루 18회)
def is_quota_ok(model_name):
    now = time.time()
    today = datetime.now().strftime('%Y-%m-%d')
    usage = model_usage[model_name]

    # 날짜 바뀌면 초기화
    if usage['day'] != today:
        usage['day'] = today
        usage['day_count'] = 0
        usage['last_calls'] = []

    # 하루 18회 제한
    if usage['day_count'] >= 18:
        return False
    
    # 1분 이내 3회 제한
    usage['last_calls'] = [t for t in usage['last_calls'] if now - t < 60]
    if len(usage['last_calls']) >= 3:
        return False
        
    return True

# 전화번호 마침표 제거 로직
def format_tel(tel_str):
    if not tel_str or "없음" in tel_str: return "없음"
    nums = re.sub(r'[^0-9]', '', tel_str)
    if len(nums) == 10:
        return f"{nums[:2]}-{nums[2:6]}-{nums[6:]}" if nums.startswith('02') else f"{nums[:3]}-{nums[3:6]}-{nums[6:]}"
    elif len(nums) == 11:
        return f"{nums[:3]}-{nums[3:7]}-{nums[7:]}"
    return tel_str

# 🚀 [추가] 상호명 정제 로직 (VCF 이름용)
def clean_org_name(org_name):
    if not org_name: return ""
    return re.sub(r'(주식회사|유한회사|\(주\)|\(유\)|COMPANY|CO\.|LTD\.|CORP\.)', '', org_name, flags=re.IGNORECASE).strip()

def create_res_template(info):
    # 🚀 [수정] 줄공백 제거 및 홈페이지 제외 로직
    lines = ["📋 명함 분석 결과", "━━━━━━━━━━━━━━"]
    lines.append(f"🏢 상호: {info.get('상호', '없음')}")
    lines.append(f"👤 대표: {info.get('대표', '없음')}")
    lines.append(f"🎖️ 직급: {info.get('직급', '없음')}")
    lines.append(f"📍 주소: {info.get('주소', '없음')}")
    lines.append(f"📞 전화: {format_tel(info.get('전화', '없음'))}")
    lines.append(f"📠 팩스: {format_tel(info.get('팩스', '없음'))}")
    lines.append(f"📧 메일: {info.get('이메일', '없음')}")
    
    if info.get('웹사이트') and info['웹사이트'] != "없음":
        lines.append(f"🌐 웹사이트: {info['웹사이트']}")
    
    lines.append("━━━━━━━━━━━━━━")
    
    # \n\n 대신 \n을 사용하여 줄공백 메움
    text = "\n".join(lines)
    
    return {
        "version": "2.0",
        "template": {
            "outputs": [{"simpleText": {"text": text}}],
            "quickReplies": [{
                "label": "📁 연락처 파일 만들기",
                "action": "message",
                "messageText": "연락처 파일 만들어줘",
                "extra": info
            }]
        }
    }

def run_analysis(client, user_text, image_url):
    global call_count
    prompt = (
        "너는 인간의 상식을 가진 세계 최고의 명함 정리 비서다. 사진을 분석하여 다음 규칙에 따라 정보를 추출하라.\n\n"
        "1. 상호: 로고 또는 사명 전체.\n"
        "2. 대표: 성함만 추출 (직급은 분리하여 '직급' 항목에 넣을 것).\n"
        "3. 직급: 부서명 또는 직위.\n"
        "4. 주소: 전체 주소.\n"
        "5. 전화: 010(휴대폰) 번호를 최우선으로 '전화'에 넣고, 휴대폰이 없으면 070이나 02 등 유선번호를 채워라.\n"
        "6. 팩스: 'F'나 'FAX' 표시가 명확한 번호만 추출하라.\n"
        "7. 이메일: @ 포함 주소.\n"
        "8. 웹사이트: 명함에 적힌 회사 홈페이지 URL.\n\n"
        "※ 주의: 확실하지 않은 정보는 '없음'으로 표기하라."
    )
    
    # 🚀 [수정] 순환하며 쿼터 남은 모델 찾기
    selected_model = None
    for _ in range(len(MODELS)):
        candidate = MODELS[call_count % len(MODELS)]
        call_count += 1
        if is_quota_ok(candidate):
            selected_model = candidate
            break
            
    if not selected_model:
        return "QUOTA_EXCEEDED"
    
    # 호출 기록 업데이트
    model_usage[selected_model]['day_count'] += 1
    model_usage[selected_model]['last_calls'].append(time.time())
    
    try:
        if image_url:
            img_res = requests.get(image_url, timeout=15)
            response = client.models.generate_content(
                model=selected_model,
                contents=[prompt, types.Part.from_bytes(data=img_res.content, mime_type="image/jpeg")]
            )
        else:
            response = client.models.generate_content(model=selected_model, contents=f"{prompt}\n\n텍스트: {user_text}")
        
        res_text = response.text.strip()
        info = {"상호": "없음", "대표": "없음", "직급": "없음", "주소": "없음", "전화": "없음", "팩스": "없음", "이메일": "없음", "웹사이트": "없음"}
        for line in res_text.splitlines():
            line = line.replace('*', '').strip()
            if ':' in line:
                parts = line.split(':', 1)
                k_raw, v_raw = parts[0].strip(), parts[1].strip()
                for key in info.keys():
                    if key in k_raw:
                        if key == "대표":
                            v_raw = re.sub(r'(\||\/|대표이사|대표|소장|기술지원|사원|대리|과장|차장|부장|본부장|이사|팀장)', '', v_raw).strip()
                        info[key] = v_raw
        return info
    except Exception:
        return {"상호": "분석지연", "대표": "재시도필요", "직급": "없음", "주소": "없음", "전화": "없음", "팩스": "없음", "이메일": "없음", "웹사이트": "없음"}

@app.route('/')
def home():
    return "Server is Live!"

@app.route('/api/get_biz_info', methods=['POST'])
@app.route('/api/get_biz_info/', methods=['POST'])
def get_biz_info():
    try:
        client = get_client()
        data = request.get_json(force=True)
        params = data.get('action', {}).get('params', {})
        client_extra = data.get('action', {}).get('clientExtra', {}) or {}
        image_url = params.get('image') or params.get('sys_plugin_image')
        callback_url = data.get('userRequest', {}).get('callbackUrl')

        if client_extra:
            name, org, job = client_extra.get('대표', '이름'), client_extra.get('상호', ''), client_extra.get('직급', '')
            tel = format_tel(client_extra.get('전화', ''))
            fax = format_tel(client_extra.get('팩스', ''))
            email, addr, web = client_extra.get('이메일', ''), client_extra.get('주소', ''), client_extra.get('웹사이트', '없음')
            
            # 🚀 [수정] VCF 상호명 정제 적용
            clean_org = clean_org_name(org)
            display_name = f"{name}({clean_org})" if clean_org else name
            
            web_entry = f"URL:{web}\r\n" if web != "없음" else ""
            
            vcf_content = (f"BEGIN:VCARD\r\nVERSION:3.0\r\n"
                           f"FN;CHARSET=UTF-8:{display_name}\r\n"
                           f"ORG;CHARSET=UTF-8:{org}\r\n"
                           f"TITLE;CHARSET=UTF-8:{job}\r\n"
                           f"TEL;TYPE=CELL,VOICE:{tel}\r\n"
                           f"TEL;TYPE=FAX:{fax}\r\n"
                           f"EMAIL:{email}\r\n"
                           f"ADR;CHARSET=UTF-8:;;{addr};;;\r\n"
                           f"{web_entry}"
                           f"NOTE;CHARSET=UTF-8:직급: {job}\r\n"
                           f"END:VCARD")
            
            fn = f"biz_{uuid.uuid4().hex[:8]}.vcf"
            with open(os.path.join(STATIC_DIR, fn), "w", encoding="utf-8") as f: f.write(vcf_content)
            
            return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": f"📂 {display_name} 연락처 저장:\n{request.host_url.rstrip('/')}/download/{fn}"}}]}})

        if not image_url:
            utterance = data.get('userRequest', {}).get('utterance', '')
            info = run_analysis(client, utterance, None)
            if info == "QUOTA_EXCEEDED":
                return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "모델 할당량이 일시적으로 소진되었습니다. 잠시 후 시도해주세요."}}]}})
            return jsonify(create_res_template(info))

        state = {"info": None, "is_timeout": False}
        def worker():
            state["info"] = run_analysis(client, "", image_url)
            if state["is_timeout"] and callback_url and state["info"] and state["info"] != "QUOTA_EXCEEDED":
                requests.post(callback_url, data=json.dumps(create_res_template(state["info"])), headers={'Content-Type': 'application/json; charset=utf-8'}, timeout=15)

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=3.5)

        if state["info"]:
            if state["info"] == "QUOTA_EXCEEDED":
                return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "오늘 준비된 분석 횟수가 모두 소진되었습니다. 내일 다시 이용해주세요! 🌙"}}]}})
            return jsonify(create_res_template(state["info"]))
        else:
            state["is_timeout"] = True
            return jsonify({"version": "2.0", "useCallback": True, "data": {"text": "명함을 정밀 분석 중입니다... ⏳"}})
    except Exception:
        return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "잠시 후 다시 시도해주세요."}}]}})

@app.route('/download/<filename>')
def download_file(filename):
    return send_from_directory(STATIC_DIR, filename, as_attachment=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
