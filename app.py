import os
import uuid
import re
import requests
import threading
import time
from flask import Flask, request, jsonify, send_from_directory
from google import genai

app = Flask(__name__)

STATIC_DIR = '/tmp/static'
os.makedirs(STATIC_DIR, exist_ok=True)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

def get_client():
    if not GEMINI_API_KEY: return None
    return genai.Client(api_key=GEMINI_API_KEY)

def format_tel(tel_str):
    if not tel_str or "없음" in tel_str: return "없음"
    found = re.search(r'[0-9]{2,4}-[0-9]{3,4}-[0-9]{4}', tel_str)
    if found: return found.group()
    clean_num = re.sub(r'[^0-9]', '', tel_str)
    return clean_num if clean_num else tel_str

# 결과 메시지 생성 공통 함수
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

# 실제 분석을 수행하는 메인 로직
def run_analysis(client, user_text, image_url):
    prompt = "명함 추출 전문가로서 상호, 대표, 주소, 전화, 팩스, 이메일을 '항목:내용' 형식으로만 출력해."
    target_model = 'gemini-flash-latest' if image_url else 'gemini-2.5-flash-lite'
    
    if image_url:
        img_res = requests.get(image_url, timeout=5)
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
                k, v = parts
                for key in info.keys():
                    if key in k:
                        info[key] = format_tel(v.strip()) if key in ['전화', '팩스'] else v.strip()
    return info

@app.route('/')
def health_check(): return "OK", 200

@app.route('/download/<filename>')
def download_file(filename):
    return send_from_directory(STATIC_DIR, filename, as_attachment=True)

# 🚀 4초 하이브리드 로직 적용
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

        # [케이스 1] 연락처 파일 생성 (즉시 응답)
        if client_extra:
            name, org = client_extra.get('name', '이름'), client_extra.get('org', '')
            vcf = f"BEGIN:VCARD\nVERSION:3.0\nFN:{name}({org})\nORG:{org}\nTEL:{client_extra.get('tel','')}\nEND:VCARD"
            fn = f"biz_{uuid.uuid4().hex[:8]}.vcf"
            with open(os.path.join(STATIC_DIR, fn), "w", encoding="utf-8") as f: f.write(vcf)
            return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": f"📂 연락처 링크:\n{request.host_url.rstrip('/')}/download/{fn}"}}]}})

        # [케이스 2] 4초 하이브리드 분석
        result_container = [] # 분석 결과를 담을 리스트

        def worker():
            try:
                info = run_analysis(client, user_text, image_url)
                result_container.append(info)
                # 만약 이미 4초가 지나서 callback 응답이 나간 상태라면, 결과를 callbackUrl로 전송
                if len(result_container) > 1: # 1번은 worker 완료 표시, 2번은 이미 callback 발송됨 의미
                    requests.post(callback_url, json=create_res_template(info), timeout=10)
            except:
                pass

        t = threading.Thread(target=worker)
        t.start()

        # 최대 4초 대기 (블로그 추천 방식)
        t.join(timeout=4.0)

        if t.is_alive():
            # 4초 안에 안 끝남 -> "분석 중" 먼저 보내고 callback으로 전환
            result_container.append("CALLBACK_SENT") 
            return jsonify({
                "version": "2.0", 
                "useCallback": True, 
                "data": {"text": "분석이 길어지고 있습니다. 잠시만 기다려 주세요! ⏳"}
            })
        else:
            # 4초 안에 끝남 -> 즉시 응답
            if result_container:
                return jsonify(create_res_template(result_container[0]))
            else:
                raise Exception("Analysis Failed")

    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "처리 중 오류가 발생했습니다."}}]}})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
