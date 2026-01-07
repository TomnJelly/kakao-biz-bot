import os
import uuid
import re
import requests
import threading
from flask import Flask, request, jsonify, send_from_directory
from google import genai

app = Flask(__name__)

# 정적 파일 저장 경로 설정
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

# 결과 템플릿 생성 함수
def create_res_template(info):
    return {
        "version": "2.0",
        "template": {
            "outputs": [{"simpleText": {"text": f"📋 분석 결과\n\n상호: {info['상호']}\n대표: {info['대표']}\n주소: {info['주소']}\n전화: {info['전화']}\n팩스: {info['팩스']}\n이메일: {info['이메일']}"}}],
            "quickReplies": [{
                "label": "📁 연락처 파일 만들기",
                "action": "message",
                "messageText": "연락처 파일 만들어줘",
                "extra": {
                    "name": info['대표'], "org": info['상호'], 
                    "tel": info['전화'], "fax": info['팩스'], 
                    "email": info['이메일'], "addr": info['주소']
                }
            }]
        }
    }

# 실제 분석 로직
def run_analysis(client, user_text, image_url):
    prompt = """명함 추출 전문가로서 아래 형식만 출력하세요.
상호:내용
대표:내용
주소:내용
전화:내용
팩스:내용
이메일:내용"""
    
    # [모델 분리] 사진은 latest, 텍스트는 lite
    target_model = 'gemini-flash-latest' if image_url else 'gemini-2.5-flash-lite'
    
    if image_url:
        img_res = requests.get(image_url, timeout=10)
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
                        val = v.strip().strip('.')
                        info[key] = format_tel(val) if key in ['전화', '팩스'] else val
    return info

@app.route('/')
def health_check(): return "OK", 200

@app.route('/download/<filename>')
def download_file(filename):
    return send_from_directory(STATIC_DIR, filename, as_attachment=True)

# 🚀 루트 두 줄 유지 + 4초 하이브리드 로직
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

        # [1] 연락처 파일 생성 로직
        if client_extra:
            name = client_extra.get('name', '이름')
            org = client_extra.get('org', '')
            tel = client_extra.get('tel', '')
            fax = client_extra.get('fax', '')
            email = client_extra.get('email', '')
            addr = client_extra.get('addr', '')
            
            display_name = f"{name}({org})" if org and org != "없음" else name
            vcf_content = (
                "BEGIN:VCARD\nVERSION:3.0\n"
                f"FN;CHARSET=UTF-8:{display_name}\n"
                f"ORG;CHARSET=UTF-8:{org}\n"
                f"TEL:{tel}\nTEL;TYPE=FAX:{fax}\n"
                f"EMAIL:{email}\nADR;CHARSET=UTF-8:;;{addr};;;\n"
                "END:VCARD"
            )
            fn = f"biz_{uuid.uuid4().hex[:8]}.vcf"
            with open(os.path.join(STATIC_DIR, fn), "w", encoding="utf-8") as f:
                f.write(vcf_content)
            
            return jsonify({
                "version": "2.0",
                "template": { "outputs": [{"simpleText": {"text": f"📂 {display_name} 연락처 링크:\n{request.host_url.rstrip('/')}/download/{fn}"}}] }
            })

        # [2] 명함 분석 로직 (4초 하이브리드)
        state = {"info": None, "callback_sent": False}

        def worker():
            try:
                # 텍스트가 비어있고 이미지도 없으면 중단
                if not user_text.strip() and not image_url:
                    return
                
                state["info"] = run_analysis(client, user_text, image_url)
                
                # 이미 4초가 지나서 "분석 중" 응답이 나갔다면 콜백 전송
                if state["callback_sent"] and callback_url:
                    requests.post(callback_url, json=create_res_template(state["info"]), timeout=10)
            except Exception as e:
                print(f"Worker Error: {e}")
                if state["callback_sent"] and callback_url:
                    requests.post(callback_url, json={"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "분석 중 오류가 발생했습니다."}}]}})

        t = threading.Thread(target=worker)
        t.start()
        
        # 3.8초 대기 (카톡 5초 제한 대비)
        t.join(timeout=3.8)

        if state["info"]:
            # 시간 내 분석 성공 -> 즉시 응답
            return jsonify(create_res_template(state["info"]))
        else:
            # 시간 초과 -> 콜백 모드로 전환
            state["callback_sent"] = True
            return jsonify({
                "version": "2.0", 
                "useCallback": True, 
                "data": {"text": "명함을 정밀 분석 중입니다. 잠시만 기다려 주세요! ⏳"}
            })

    except Exception as e:
        print(f"Main Error: {e}")
        return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "서버 오류가 발생했습니다."}}]}})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
