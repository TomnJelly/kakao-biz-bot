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

# 파일 저장 경로 설정
STATIC_DIR = '/tmp/static'
os.makedirs(STATIC_DIR, exist_ok=True)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# 🚀 [부하 분산] 모델별 20회 제한을 피하기 위한 3중 교차 설정
call_count = 0
MODELS = ['gemini-3-flash-preview', 'gemini-2.5-flash', 'gemini-2.5-flash-lite']

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

def create_res_template(info):
    text = (
        f"📋 명함 분석 결과\n"
        f"━━━━━━━━━━━━━━\n"
        f"🏢 상호: {info['상호']}\n\n"
        f"👤 대표: {info['대표']}\n"
        f"🎖️ 직급: {info['직급']}\n\n"
        f"📍 주소: {info['주소']}\n\n"
        f"📞 전화: {info['전화']}\n\n"
        f"📠 팩스: {info['팩스']}\n\n"
        f"📧 메일: {info['이메일']}\n"
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
    global call_count
    
    # 🎯 [알잘딱깔센 프롬프트] 이름과 직급을 확실히 분리 추출
    prompt = (
        "명함 사진을 분석하여 다음 항목을 추출하라. 불필요한 설명 없이 '항목: 내용' 형식으로만 답하라.\n\n"
        "1. 상호: 회사명\n"
        "2. 대표: 사람 이름만 추출(직함/직급 제외)\n"
        "3. 직급: 직위나 부서명 (예: 소장, 기술지원, 대표이사 등)\n"
        "4. 주소: 전체 주소\n"
        "5. 전화: 휴대폰 또는 대표번호 (숫자와 하이픈만)\n"
        "6. 팩스: 팩스 번호\n"
        "7. 이메일: 이메일 주소\n\n"
        "※ 주의: 이름과 직급을 반드시 분리하고, 정보가 없으면 '없음'으로 표시."
    )
    
    selected_model = MODELS[call_count % 3]
    call_count += 1
    
    try:
        if image_url:
            img_res = requests.get(image_url, timeout=15)
            response = client.models.generate_content(
                model=selected_model,
                contents=[prompt, types.Part.from_bytes(data=img_res.content, mime_type="image/jpeg")]
            )
        else:
            response = client.models.generate_content(
                model=selected_model,
                contents=f"{prompt}\n\n텍스트: {user_text}"
            )
        
        res_text = response.text.strip()
        info = {"상호": "없음", "대표": "없음", "직급": "없음", "주소": "없음", "전화": "없음", "팩스": "없음", "이메일": "없음"}
        
        for line in res_text.splitlines():
            line = line.replace('*', '').strip()
            if ':' in line:
                parts = line.split(':', 1)
                k_raw, v_raw = parts[0].strip(), parts[1].strip()
                for key in info.keys():
                    if key in k_raw:
                        # 이름에서 직함 중복 제거 필터링
                        if key == "대표":
                            v_raw = re.sub(r'(대표이사|대표|소장|기술지원|사원|대리|과장|차장|부장|본부장|이사)', '', v_raw).strip()
                        info[key] = format_tel(v_raw) if key in ['전화', '팩스'] else v_raw
        return info
    except Exception as e:
        print(f"Error ({selected_model}): {e}")
        return {"상호": "분석지연", "대표": "재시도필요", "직급": "없음", "주소": "없음", "전화": "없음", "팩스": "없음", "이메일": "없음"}

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

        # 📂 [VCF 생성 로직] 직급을 TITLE과 NOTE에 추가
        if client_extra:
            name = client_extra.get('대표', '이름')
            org = client_extra.get('상호', '')
            job = client_extra.get('직급', '')
            tel = client_extra.get('전화', '')
            fax = client_extra.get('팩스', '')
            email = client_extra.get('이메일', '')
            addr = client_extra.get('주소', '')
            
            # VCF 파일 내용 구성
            vcf_content = (
                f"BEGIN:VCARD\r\n"
                f"VERSION:3.0\r\n"
                f"FN;CHARSET=UTF-8:{name}\r\n"
                f"ORG;CHARSET=UTF-8:{org}\r\n"
                f"TITLE;CHARSET=UTF-8:{job}\r\n"
                f"TEL;TYPE=CELL,VOICE:{tel}\r\n"
                f"TEL;TYPE=FAX:{fax}\r\n"
                f"EMAIL:{email}\r\n"
                f"ADR;CHARSET=UTF-8:;;{addr};;;\r\n"
                f"NOTE;CHARSET=UTF-8:직급: {job}\r\n"
                f"END:VCARD"
            )
            fn = f"biz_{uuid.uuid4().hex[:8]}.vcf"
            with open(os.path.join(STATIC_DIR, fn), "w", encoding="utf-8") as f:
                f.write(vcf_content)
            
            return jsonify({
                "version": "2.0",
                "template": {
                    "outputs": [{"simpleText": {"text": f"📂 {name}({org}) 연락처 저장 링크:\n{request.host_url.rstrip('/')}/download/{fn}"}}]
                }
            })

        # 1. 텍스트 직접 입력 시
        if not image_url:
            utterance = data.get('userRequest', {}).get('utterance', '')
            info = run_analysis(client, utterance, None)
            return jsonify(create_res_template(info))

        # 2. 이미지 전송 시 (3.8초 룰 적용)
        state = {"info": None, "is_timeout": False}
        def worker():
            state["info"] = run_analysis(client, "", image_url)
            if state["is_timeout"] and callback_url and state["info"]:
                headers = {'Content-Type': 'application/json; charset=utf-8'}
                requests.post(callback_url, data=json.dumps(create_res_template(state["info"])), headers=headers, timeout=15)

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=3.8)

        if state["info"]:
            return jsonify(create_res_template(state["info"]))
        else:
            state["is_timeout"] = True
            return jsonify({"version": "2.0", "useCallback": True, "data": {"text": "명함을 정밀 분석 중입니다... ⏳"}})

    except Exception as e:
        return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "잠시 후 다시 시도해주세요."}}]}})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
