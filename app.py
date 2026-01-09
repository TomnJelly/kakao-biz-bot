import os
import uuid
import re
import requests
import threading
import json
from flask import Flask, request, jsonify, send_from_directory
from google import genai

app = Flask(__name__)

STATIC_DIR = '/tmp/static'
os.makedirs(STATIC_DIR, exist_ok=True)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

def get_client():
    if not GEMINI_API_KEY: return None
    return genai.Client(api_key=GEMINI_API_KEY)

# 전화번호 하이픈 보정 (02-945-9174 등)
def format_tel(tel_str):
    if not tel_str or "없음" in tel_str: return "없음"
    nums = re.sub(r'[^0-9]', '', tel_str)
    if len(nums) == 10:
        return f"{nums[:2]}-{nums[2:6]}-{nums[6:]}" if nums.startswith('02') else f"{nums[:3]}-{nums[3:6]}-{nums[6:]}"
    elif len(nums) == 11:
        return f"{nums[:3]}-{nums[3:7]}-{nums[7:]}"
    elif len(nums) == 9 and nums.startswith('02'):
        return f"{nums[:2]}-{nums[2:5]}-{nums[5:]}"
    return tel_str

def create_res_template(info):
    return {
        "version": "2.0",
        "template": {
            "outputs": [{"simpleText": {"text": f"📋 분석 결과\n\n상호: {info['상호']}\n대표: {info['대표']}\n주소: {info['주소']}\n전화: {info['전화']}\n팩스: {info['팩스']}\n이메일: {info['이메일']}"}}],
            "quickReplies": [{
                "label": "📁 연락처 파일 만들기",
                "action": "message",
                "messageText": "연락처 파일 만들어줘",
                "extra": {"name": info['대표'], "org": info['상호'], "tel": info['전화'], "fax": info['팩스'], "email": info['이메일'], "addr": info['주소']}
            }]
        }
    }

def run_analysis(client, user_text, image_url):
    prompt = "명함 추출 전문가로서 상호, 대표, 주소, 전화, 팩스, 이메일을 추출해. 전화/팩스는 010-0000-0000 형식처럼 하이픈(-)을 꼭 넣어."
    target_model = 'gemini-flash-latest' if image_url else 'gemini-2.5-flash-lite'
    
    if image_url:
        img_res = requests.get(image_url, timeout=15)
        response = client.models.generate_content(
            model=target_model,
            contents=[prompt, {"mime_type": "image/jpeg", "data": img_res.content}]
        )
    else:
        response = client.models.generate_content(
            model=target_model, contents=f"{prompt}\n\n텍스트: {user_text}"
        )
    
    res_text = response.text.strip()
    info = {"상호": "없음", "대표": "없음", "주소": "없음", "전화": "없음", "팩스": "없음", "이메일": "없음"}
    for line in res_text.splitlines():
        if ':' in line:
            parts = line.split(':', 1)
            if len(parts) == 2:
                k, v = parts[0].strip(), parts[1].strip()
                for key in info.keys():
                    if key in k:
                        info[key] = format_tel(v) if key in ['전화', '팩스'] else v
    return info

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

        # [1] 연락처 파일 생성 (버그 수정됨)
        if client_extra:
            name, org = client_extra.get('name', '이름'), client_extra.get('org', '')
            tel, fax, email, addr = client_extra.get('tel', ''), client_extra.get('fax', ''), client_extra.get('email', ''), client_extra.get('addr', '')
            display_name = f"{name}({org})" if org and org != "없음" else name
            vcf_content = f"BEGIN:VCARD\nVERSION:3.0\nFN;CHARSET=UTF-8:{display_name}\nORG;CHARSET=UTF-8:{org}\nTEL;TYPE=CELL,VOICE:{tel}\nTEL;TYPE=FAX:{fax}\nEMAIL:{email}\nADR;CHARSET=UTF-8:;;{addr};;;\nEND:VCARD"
            fn = f"biz_{uuid.uuid4().hex[:8]}.vcf"
            with open(os.path.join(STATIC_DIR, fn), "w", encoding="utf-8") as f: f.write(vcf_content)
            return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": f"📂 {display_name} 연락처 링크:\n{request.host_url.rstrip('/')}/download/{fn}"}}]}})

        # [2] 분석 모드
        # (A) 텍스트: 즉시 응답
        if not image_url:
            info = run_analysis(client, user_text, None)
            return jsonify(create_res_template(info))

        # (B) 이미지: 3.8초 하이브리드 대기 및 콜백 전송
        state = {"info": None, "callback_sent": False}
        
        def worker():
            try:
                state["info"] = run_analysis(client, user_text, image_url)
                
                if state["callback_sent"] and callback_url:
                    # 🚀 중요: 카카오 콜백은 Content-Type을 명시해야 안전함
                    headers = {'Content-Type': 'application/json; charset=utf-8'}
                    res = requests.post(
                        callback_url, 
                        data=json.dumps(create_res_template(state["info"])), 
                        headers=headers,
                        timeout=10
                    )
                    print(f"Callback Response: {res.status_code}, {res.text}")
            except Exception as e:
                print(f"Worker Error: {e}")

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=3.8)

        if state["info"]:
            return jsonify(create_res_template(state["info"]))
        else:
            state["callback_sent"] = True
            # useCallback 설정 시 200 응답 필수
            return jsonify({
                "version": "2.0",
                "useCallback": True,
                "data": {"text": "명함을 정밀 분석 중입니다. 1분 내로 답장을 드릴게요! ⏳"}
            })

    except Exception as e:
        print(f"Main Error: {e}")
        return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "서버 오류 발생"}}]}})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
