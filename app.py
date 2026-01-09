import os
import uuid
import re
import requests
import threading
import json
from flask import Flask, request, jsonify, send_from_directory
from google import genai
from google.genai import types

app = Flask(__name__)

# 정적 파일 저장 경로
STATIC_DIR = '/tmp/static'
os.makedirs(STATIC_DIR, exist_ok=True)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

def get_client():
    if not GEMINI_API_KEY: return None
    return genai.Client(api_key=GEMINI_API_KEY)

# 전화번호 하이픈 보정
def format_tel(tel_str):
    if not tel_str or "없음" in tel_str: return "없음"
    nums = re.sub(r'[^0-9]', '', tel_str)
    if len(nums) == 10:
        return f"{nums[:2]}-{nums[2:6]}-{nums[6:]}" if nums.startswith('02') else f"{nums[:3]}-{nums[3:6]}-{nums[6:]}"
    elif len(nums) == 11:
        return f"{nums[:3]}-{nums[3:7]}-{nums[7:]}"
    return tel_str

# [사용자님 기존 설정 복구] 이미지: latest / 텍스트: 2.5-lite
MODEL_PHOTO = 'gemini-flash-latest'
MODEL_TEXT = 'gemini-2.5-flash-lite'

def run_analysis(client, user_text, image_url):
    prompt = "명함 사진에서 상호, 대표, 주소, 전화, 팩스, 이메일을 추출해. '항목: 내용' 형식으로 쓰고 없는 항목은 '없음' 표시. 전화/팩스는 하이픈 포함."
    
    try:
        if image_url:
            img_res = requests.get(image_url, timeout=15)
            # "없음" 에러 해결을 위한 표준 규격 데이터 구성
            response = client.models.generate_content(
                model=MODEL_PHOTO, # 기존 모델 유지
                contents=[
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_text(text=prompt),
                            types.Part.from_bytes(data=img_res.content, mime_type="image/jpeg")
                        ]
                    )
                ]
            )
        else:
            response = client.models.generate_content(
                model=MODEL_TEXT, # 기존 모델 유지
                contents=f"{prompt}\n\n텍스트: {user_text}"
            )
        
        res_text = response.text.strip()
        info = {"상호": "없음", "대표": "없음", "주소": "없음", "전화": "없음", "팩스": "없음", "이메일": "없음"}
        for line in res_text.splitlines():
            line = line.replace('*', '').strip()
            if ':' in line:
                parts = line.split(':', 1)
                if len(parts) == 2:
                    k, v = parts[0].strip(), parts[1].strip()
                    for key in info.keys():
                        if key in k:
                            info[key] = format_tel(v) if key in ['전화', '팩스'] else v
        return info
    except Exception as e:
        print(f"Analysis Error: {e}")
        return {"상호": "분석오류", "대표": "분석오류", "주소": "분석오류", "전화": "분석오류", "팩스": "분석오류", "이메일": "분석오류"}

# --- 이하 @app.route 로직은 사용자님 기존 코드와 동일 (콜백 안정화만 포함) ---

@app.route('/')
def health_check(): return "OK", 200

@app.route('/download/<filename>')
def download_file(filename):
    return send_from_directory(STATIC_DIR, filename, as_attachment=True)

@app.route('/api/get_biz_info', methods=['POST'])
@app.route('/api/get_biz_info/', methods=['POST'])
def get_biz_info():
    try:
        client = get_client()
        data = request.get_json(force=True)
        params = data.get('action', {}).get('params', {})
        user_text = params.get('user_input') or data.get('userRequest', {}).get('utterance', '')
        client_extra = data.get('action', {}).get('clientExtra', {}) or {}
        image_url = params.get('image') or params.get('sys_plugin_image')
        callback_url = data.get('userRequest', {}).get('callbackUrl')

        if client_extra:
            name, org = client_extra.get('name', '이름'), client_extra.get('org', '')
            tel, fax, email, addr = client_extra.get('tel', ''), client_extra.get('fax', ''), client_extra.get('email', ''), client_extra.get('addr', '')
            display_name = f"{name}({org})" if org and org != "없음" else name
            vcf_content = f"BEGIN:VCARD\nVERSION:3.0\nFN;CHARSET=UTF-8:{display_name}\nORG;CHARSET=UTF-8:{org}\nTEL;TYPE=CELL,VOICE:{tel}\nTEL;TYPE=FAX:{fax}\nEMAIL:{email}\nADR;CHARSET=UTF-8:;;{addr};;;\nEND:VCARD"
            fn = f"biz_{uuid.uuid4().hex[:8]}.vcf"
            with open(os.path.join(STATIC_DIR, fn), "w", encoding="utf-8") as f: f.write(vcf_content)
            return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": f"📂 {display_name} 연락처 링크:\n{request.host_url.rstrip('/')}/download/{fn}"}}]}})

        if not image_url:
            info = run_analysis(client, user_text, None)
            return jsonify({
                "version": "2.0",
                "template": {
                    "outputs": [{"simpleText": {"text": f"📋 분석 결과\n\n상호: {info['상호']}\n대표: {info['대표']}\n주소: {info['주소']}\n전화: {info['전화']}\n팩스: {info['팩스']}\n이메일: {info['이메일']}"}}],
                    "quickReplies": [{"label": "📁 연락처 파일 만들기", "action": "message", "messageText": "연락처 파일 만들어줘", "extra": info}]
                }
            })

        state = {"info": None, "callback_sent": False}
        def worker():
            try:
                state["info"] = run_analysis(client, user_text, image_url)
                if state["callback_sent"] and callback_url:
                    headers = {'Content-Type': 'application/json; charset=utf-8'}
                    res_body = {
                        "version": "2.0",
                        "template": {
                            "outputs": [{"simpleText": {"text": f"📋 분석 결과\n\n상호: {state['info']['상호']}\n대표: {state['info']['대표']}\n주소: {state['info']['주소']}\n전화: {state['info']['전화']}\n팩스: {state['info']['팩스']}\n이메일: {state['info']['이메일']}"}}],
                            "quickReplies": [{"label": "📁 연락처 파일 만들기", "action": "message", "messageText": "연락처 파일 만들어줘", "extra": state['info']}]
                        }
                    }
                    requests.post(callback_url, data=json.dumps(res_body), headers=headers, timeout=15)
            except Exception as e:
                print(f"Worker Error: {e}")

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=3.8)

        if state["info"]:
            # template 생성 부분 중복 제거를 위해 직접 작성
            res_info = state["info"]
            return jsonify({
                "version": "2.0",
                "template": {
                    "outputs": [{"simpleText": {"text": f"📋 분석 결과\n\n상호: {res_info['상호']}\n대표: {res_info['대표']}\n주소: {res_info['주소']}\n전화: {res_info['전화']}\n팩스: {res_info['팩스']}\n이메일: {res_info['이메일']}"}}],
                    "quickReplies": [{"label": "📁 연락처 파일 만들기", "action": "message", "messageText": "연락처 파일 만들어줘", "extra": res_info}]
                }
            })
        else:
            state["callback_sent"] = True
            return jsonify({"version": "2.0", "useCallback": True, "data": {"text": "명함을 분석 중입니다... ⏳"}})

    except Exception as e:
        return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "서버 오류 발생"}}]}})

# ... (위쪽 기존 코드들은 그대로 두세요) ...

def check_available_models():
    client = get_client()
    if not client:
        print("❌ API 키가 설정되지 않았습니다.")
        return
    print("\n" + "="*50)
    print("🔍 [디버깅] 현재 사용 가능한 모델 목록")
    print("="*50)
    try:
        for model in client.models.list():
            # 모델의 ID(이름)만 깔끔하게 출력합니다.
            print(f"👉 사용 가능 모델명: {model.name}")
        print("="*50 + "\n")
    except Exception as e:
        print(f"❌ 모델 목록 가져오기 실패: {e}")

if __name__ == '__main__':
    # 1. 서버가 켜지자마자 로그에 모델 목록을 출력합니다.
    check_available_models() 
    
    # 2. 그 다음 실제 서버를 실행합니다.
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
