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

STATIC_DIR = '/tmp/static'
os.makedirs(STATIC_DIR, exist_ok=True)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

def get_client():
    if not GEMINI_API_KEY: return None
    return genai.Client(api_key=GEMINI_API_KEY)

def format_tel(tel_str):
    if not tel_str or "없음" in tel_str: return "없음"
    nums = re.sub(r'[^0-9]', '', tel_str)
    if len(nums) == 10:
        return f"{nums[:2]}-{nums[2:6]}-{nums[6:]}" if nums.startswith('02') else f"{nums[:3]}-{nums[3:6]}-{nums[6:]}"
    elif len(nums) == 11:
        return f"{nums[:3]}-{nums[3:7]}-{nums[7:]}"
    return tel_str

# [사용자님 모델 목록 기반 설정]
MODEL_FOR_IMAGE = 'gemini-2.5-pro'       # 가장 똑똑한 모델
MODEL_FOR_TEXT = 'gemini-2.5-flash-lite' # 기존 선호 모델

def run_analysis(client, user_text, image_url):
    prompt = "명함 사진에서 상호, 대표, 주소, 전화, 팩스, 이메일을 추출해. '항목: 내용' 형식으로 쓰고 없는 항목은 '없음' 표시. 전화/팩스는 하이픈 포함."
    
    try:
        if image_url:
            img_res = requests.get(image_url, timeout=15)
            # 🚀 2.5 Pro 모델에 최적화된 데이터 전송
            response = client.models.generate_content(
                model=MODEL_FOR_IMAGE,
                contents=[
                    prompt,
                    types.Part.from_bytes(data=img_res.content, mime_type="image/jpeg")
                ]
            )
        else:
            response = client.models.generate_content(
                model=MODEL_FOR_TEXT,
                contents=f"{prompt}\n\n텍스트: {user_text}"
            )
        
        res_text = response.text.strip()
        info = {"상호": "없음", "대표": "없음", "주소": "없음", "전화": "없음", "팩스": "없음", "이메일": "없음"}
        for line in res_text.splitlines():
            line = line.replace('*', '').strip()
            if ':' in line:
                k, v = line.split(':', 1)
                for key in info.keys():
                    if key in k:
                        info[key] = format_tel(v.strip()) if key in ['전화', '팩스'] else v.strip()
        return info
    except Exception as e:
        print(f"Analysis Error: {e}")
        return {"상호": "분석실패", "대표": "분석실패", "주소": "분석실패", "전화": "분석실패", "팩스": "분석실패", "이메일": "분석실패"}

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
            info = run_analysis(client, data.get('userRequest', {}).get('utterance', ''), None)
            return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": str(info)}}]}})

        state = {"info": None, "callback_sent": False}
        def worker():
            try:
                state["info"] = run_analysis(client, "", image_url)
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
            return jsonify({"version": "2.0", "useCallback": True, "data": {"text": "명함을 정밀 분석 중입니다... ⏳"}})

    except Exception as e:
        return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "서버 오류 발생"}}]}})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
