import os
import uuid
import re
import requests
from flask import Flask, request, jsonify, send_from_directory
from google import genai  # 최신 라이브러리 표준

app = Flask(__name__)

STATIC_DIR = '/tmp/static'
os.makedirs(STATIC_DIR, exist_ok=True)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# 최신 SDK 클라이언트 초기화
def get_client():
    if not GEMINI_API_KEY: return None
    return genai.Client(api_key=GEMINI_API_KEY)

def format_tel(tel_str):
    if not tel_str or "없음" in tel_str: return "없음"
    # 하이픈 포함된 번호 1개만 추출
    found = re.search(r'[0-9]{2,4}-[0-9]{3,4}-[0-9]{4}', tel_str)
    if found: return found.group()
    clean_num = re.sub(r'[^0-9]', '', tel_str)
    return clean_num if clean_num else tel_str

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
        if not client: raise Exception("API Key Missing")

        data = request.get_json(force=True)
        params = data.get('action', {}).get('params', {})
        user_text = params.get('user_input') or data.get('userRequest', {}).get('utterance', '')
        client_extra = data.get('action', {}).get('clientExtra', {}) or {}
        image_url = params.get('image') or params.get('sys_plugin_image')

        # --- [모드 1] VCF 연락처 생성 (기존 로직 유지) ---
        if client_extra:
            name = client_extra.get('name', '이름없음')
            org = str(client_extra.get('org', '')).strip('.')
            tel = client_extra.get('tel', '')
            fax = client_extra.get('fax', '')
            email = client_extra.get('email', '')
            addr = client_extra.get('addr', '')

            display_name = f"{name}({org})" if org and org != "없음" else name
            vcf_content = (
                "BEGIN:VCARD\nVERSION:3.0\n"
                f"FN;CHARSET=UTF-8:{display_name}\n"
                f"N;CHARSET=UTF-8:{display_name};;;;\n"
                f"ORG;CHARSET=UTF-8:{org}\n"
                f"item1.TEL:{tel}\nitem1.X-ABLabel:전화번호\n"
                f"item2.TEL:{fax}\nitem2.X-ABLabel:팩스번호\n"
                f"item3.EMAIL;TYPE=INTERNET:{email}\nitem3.X-ABLabel:이메일\n"
                f"item4.ADR;CHARSET=UTF-8:;;{addr};;;\nitem4.X-ABLabel:주소\n"
                "END:VCARD"
            )
            
            file_name = f"biz_{uuid.uuid4().hex[:8]}.vcf"
            with open(os.path.join(STATIC_DIR, file_name), "w", encoding="utf-8") as f:
                f.write(vcf_content)

            return jsonify({
                "version": "2.0",
                "template": { "outputs": [{"simpleText": {"text": f"📂 {display_name} 연락처 링크:\n{request.host_url.rstrip('/')}/download/{file_name}"}}] }
            })

        # --- [모드 2] 명함 분석 (이미지 + 텍스트 모두 포함) ---
        prompt = """명함 추출 전문가로서 아래 형식만 출력하세요.
- 상호: 회사명 (마침표 없이)
- 대표: 성함만
- 주소: 도로명/지번 주소 전체
- 전화: 하이픈 포함 번호 1개만
- 팩스: 번호 1개만 (없으면 없음)
- 이메일: 이메일 주소

형식:
상호:내용
대표:내용
주소:내용
전화:내용
팩스:내용
이메일:내용"""

        if image_url:
            # 사진 분석 로직 복구
            img_res = requests.get(image_url, timeout=5)
            response = client.models.generate_content(
                model='gemini-2.0-flash',
                contents=[prompt, {"mime_type": "image/jpeg", "data": img_res.content}]
            )
        else:
            # 텍스트 분석 로직
            if not user_text.strip():
                return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "내용을 입력해주세요."}}]}})
            response = client.models.generate_content(
                model='gemini-flash-latest',
                contents=f"{prompt}\n\n텍스트: {user_text}"
            )

        res_text = response.text.strip()
        info = {"상호": "없음", "대표": "없음", "주소": "없음", "전화": "없음", "팩스": "없음", "이메일": "없음"}
        for line in res_text.splitlines():
            if ':' in line:
                parts = line.split(':', 1)
                if len(parts) == 2:
                    k_raw, v_raw = parts
                    for key in info.keys():
                        if key in k_raw:
                            val = v_raw.strip().strip('.')
                            info[key] = format_tel(val) if key in ['전화', '팩스'] else val

        return jsonify({
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
        })

    except Exception as e:
        print(f"!!! Error Occurred: {e}") 
        return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "분석 중 오류가 발생했습니다. 다시 시도해 주세요."}}]}})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
