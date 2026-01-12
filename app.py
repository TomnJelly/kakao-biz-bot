import os
import uuid
import re
import requests
import threading
import json
import time  # 시간 체크용 추가
from flask import Flask, request, jsonify, send_from_directory
from google import genai
from google.genai import types

app = Flask(__name__)

# 경로 설정
STATIC_DIR = '/tmp/static'
os.makedirs(STATIC_DIR, exist_ok=True)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# 🚀 [수정] 모델 분산 호출을 위한 전역 변수
call_count = 0
MODELS = ['gemini-3-flash-preview', 'gemini-2.5-flash', 'gemini-2.5-flash-lite']
last_request_time = {}

# 🚀 [추가] 서버 자체 깨우기 (Self-Ping) 로직
def keep_alive():
    time.sleep(30) # 서버 시작 후 대기
    while True:
        try:
            # Render 환경변수 혹은 실제 주소 사용
            url = os.environ.get("RENDER_EXTERNAL_URL") or f"https://{request.host}"
            if "onrender.com" in url:
                requests.get(url, timeout=10)
        except:
            pass
        time.sleep(600) # 10분마다 실행

threading.Thread(target=keep_alive, daemon=True).start()

def get_client():
    if not GEMINI_API_KEY: return None
    return genai.Client(api_key=GEMINI_API_KEY)

def format_tel_clean(tel_str):
    if not tel_str or "없음" in tel_str: return "없음"
    nums = re.sub(r'[^0-9]', '', tel_str)
    if len(nums) == 10:
        return f"{nums[:2]}-{nums[2:6]}-{nums[6:]}" if nums.startswith('02') else f"{nums[:3]}-{nums[3:6]}-{nums[6:]}"
    elif len(nums) == 11:
        return f"{nums[:3]}-{nums[3:7]}-{nums[7:]}"
    return tel_str

def clean_org_name(org_name):
    if not org_name: return ""
    # ✅ 상호에서 컴퍼니, 주식회사 등 제거 (VCF 이름용)
    return re.sub(r'(주식회사|유한회사|\(주\)|\(유\)|COMPANY|CO\.|LTD\.|CORP\.)', '', org_name, flags=re.IGNORECASE).strip()

def create_res_template(info):
    # ✅ 분석 결과 줄 간격 제거
    web_line = f"🌐 웹사이트: {info['웹사이트']}\n" if info.get('웹사이트') and info['웹사이트'] != "없음" else ""
    tel = format_tel_clean(info.get('전화', '없음'))
    fax = format_tel_clean(info.get('팩스', '없음'))

    text = (
        f"📋 명함 분석 결과\n"
        f"━━━━━━━━━━━━━━\n"
        f"🏢 상호: {info['상호']}\n"
        f"👤 대표: {info['대표']}\n"
        f"🎖️ 직급: {info['직급']}\n"
        f"📍 주소: {info['주소']}\n"
        f"📞 전화: {tel}\n"
        f"📠 팩스: {fax}\n"
        f"📧 메일: {info['이메일']}\n"
        f"{web_line}"
        f"━━━━━━━━━━━━━━"
    )
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
    global call_count  # ✅ 전역 변수 사용 명시
    
    # 🚀 모델 순환 선택 로직
    selected_model = MODELS[call_count % len(MODELS)]
    call_count += 1  # 호출할 때마다 카운트 증가
    
    prompt = (
        "너는 세계 최고의 명함 정리 비서다. 다음 규칙에 따라 정보를 추출하라.\n"
        "1. 전화: 설명 없이 번호만 추출하라.\n"
        "2. 대표: 성함만 추출.\n"
        "추출 형식 - 상호: 내용, 대표: 내용, 직급: 내용, 주소: 내용, 전화: 내용, 팩스: 내용, 이메일: 내용, 웹사이트: 내용"
    )
    
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
            if ':' in line:
                parts = line.replace('*', '').split(':', 1)
                k, v = parts[0].strip(), parts[1].strip()
                for key in info.keys():
                    if key in k:
                        if key == "대표": v = re.sub(r'(대표이사|대표|소장|이사|팀장)', '', v).strip()
                        info[key] = v
        return info
    except Exception:
        return {"상호": "분석지연", "대표": "재시도필요", "직급": "없음", "주소": "없음", "전화": "없음", "팩스": "없음", "이메일": "없음", "웹사이트": "없음"}

@app.route('/')
def home(): return "Server is Active!"

@app.route('/api/get_biz_info', methods=['POST'])
def get_biz_info():
    global last_request_time
    try:
        client = get_client()
        data = request.get_json(force=True)
        user_id = data.get('userRequest', {}).get('user', {}).get('id', 'anonymous')
        current_time = time.time()

        # ✅ 3초 이내 중복 요청 방지 (연타 쿼터 낭비 차단)
        if user_id in last_request_time and current_time - last_request_time[user_id] < 3:
            return jsonify({"version": "2.0", "template": {"outputs": []}})
        last_request_time[user_id] = current_time

        params = data.get('action', {}).get('params', {})
        client_extra = data.get('action', {}).get('clientExtra', {}) or {}
        image_url = params.get('image') or params.get('sys_plugin_image')

        if client_extra:
            name = client_extra.get('대표', '이름')
            raw_org = client_extra.get('상호', '')
            clean_org = clean_org_name(raw_org)
            job = client_extra.get('직급', '')
            tel = format_tel_clean(client_extra.get('전화', ''))
            fax = format_tel_clean(client_extra.get('팩스', ''))
            email, addr, web = client_extra.get('이메일', ''), client_extra.get('주소', ''), client_extra.get('웹사이트', '없음')
            
            # ✅ VCF 이름 형식: 이름(상호-수식어제외)
            display_name = f"{name}({clean_org})" if clean_org else name
            web_entry = f"URL:{web}\r\n" if web != "없음" else ""
            
            vcf_content = (f"BEGIN:VCARD\r\nVERSION:3.0\r\n"
                           f"FN;CHARSET=UTF-8:{display_name}\r\n"
                           f"ORG;CHARSET=UTF-8:{raw_org}\r\n"
                           f"TITLE;CHARSET=UTF-8:{job}\r\n"
                           f"TEL;TYPE=CELL,VOICE:{tel}\r\n"
                           f"TEL;TYPE=FAX:{fax}\r\n"
                           f"EMAIL:{email}\r\n"
                           f"ADR;CHARSET=UTF-8:;;{addr};;;\r\n"
                           f"{web_entry}END:VCARD")
            
            fn = f"biz_{uuid.uuid4().hex[:8]}.vcf"
            with open(os.path.join(STATIC_DIR, fn), "w", encoding="utf-8") as f: f.write(vcf_content)
            return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": f"📂 {display_name} 연락처 저장:\n{request.host_url.rstrip('/')}/download/{fn}"}}]}})

        if not image_url:
            info = run_analysis(client, data.get('userRequest', {}).get('utterance', ''), None)
            return jsonify(create_res_template(info))

        state = {"info": None}
        def worker(): state["info"] = run_analysis(client, "", image_url)
        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=3.5)
        return jsonify(create_res_template(state["info"])) if state["info"] else jsonify({"version": "2.0", "useCallback": True, "data": {"text": "분석 중... ⏳"}})

    except Exception:
        return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "오류 발생. 다시 시도해주세요."}}]}})

@app.route('/download/<filename>')
def download_file(filename):
    return send_from_directory(STATIC_DIR, filename, as_attachment=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
