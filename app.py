import os
import uuid
import re
import requests
from flask import Flask, request, jsonify, send_from_directory
import google.generativeai as genai

app = Flask(__name__)

STATIC_DIR = '/tmp/static'
os.makedirs(STATIC_DIR, exist_ok=True)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

def get_model():
    if not GEMINI_API_KEY: return None
    genai.configure(api_key=GEMINI_API_KEY)
    return genai.GenerativeModel('models/gemini-flash-latest')

def format_tel(tel_str):
    if not tel_str or "없음" in tel_str: return ""
    nums = re.sub(r'[^0-9]', '', tel_str)
    if len(nums) == 9: return f"{nums[0:2]}-{nums[2:5]}-{nums[5:]}"
    elif len(nums) == 10:
        if nums.startswith('02'): return f"{nums[0:2]}-{nums[2:6]}-{nums[6:]}"
        else: return f"{nums[0:3]}-{nums[3:6]}-{nums[6:]}"
    elif len(nums) == 11: return f"{nums[0:3]}-{nums[3:7]}-{nums[7:]}"
    return tel_str

@app.route('/')
def health_check():
    return "OK", 200

@app.route('/download/<filename>')
def download_file(filename):
    return send_from_directory(STATIC_DIR, filename, as_attachment=True)

@app.route('/api/get_biz_info', methods=['POST'])
@app.route('/api/get_biz_info/', methods=['POST'])
def get_biz_info():
    try:
        model = get_model()
        data = request.get_json(force=True)
        user_utterance = data.get('userRequest', {}).get('utterance', '')
        params = data.get('action', {}).get('params', {})
        client_extra = data.get('action', {}).get('clientExtra', {}) or {}

        # [연락처 생성 모드]
        if "연락처" in user_utterance.replace(" ","") or client_extra:
            name = str(client_extra.get('name', '')).strip()
            org = str(client_extra.get('org', '')).strip()
            # 이름 형식 최적화
            display_name = f"{name}({org})" if (name and org and name!='없음' and org!='없음') else (name if name!='없음' else org)
            if not display_name or display_name == '없음': display_name = "신규연락처"

            vcf_lines = [
                "BEGIN:VCARD", "VERSION:3.0",
                f"FN;CHARSET=UTF-8:{display_name}",
                f"N;CHARSET=UTF-8:{display_name};;;;",
                f"ORG;CHARSET=UTF-8:{org if org!='없음' else ''}",
                f"TEL;TYPE=CELL:{client_extra.get('tel', '')}",
                f"TEL;TYPE=FAX:{client_extra.get('fax', '')}",
                f"EMAIL;TYPE=INTERNET:{client_extra.get('email', '')}",
                f"ADR;CHARSET=UTF-8:;;{client_extra.get('addr', '')};;;",
                "END:VCARD"
            ]
            file_name = f"biz_{uuid.uuid4().hex[:8]}.vcf"
            file_path = os.path.join(STATIC_DIR, file_name)
            with open(file_path, "w", encoding="utf-8") as f: f.write("\n".join(vcf_lines))
            
            return jsonify({
                "version": "2.0",
                "template": {"outputs": [{"simpleText": {"text": f"✅ 연락처 준비 완료\n👤 저장명: {display_name}\n🔗 다운로드: {request.host_url.rstrip('/')}/download/{file_name}"}}]}
            })

        # [명함 분석 모드]
        image_url = params.get('image') or params.get('sys_plugin_image')
        prompt = """명함/사업자정보에서 '상호, 대표, 주소, 전화, 팩스, 이메일'을 추출해. 
        형식은 반드시 '항목:내용'으로만 줄바꿈해서 작성해. 없으면 '없음'이라고 적어."""

        if image_url:
            img_res = requests.get(image_url, timeout=5)
            response = model.generate_content([prompt, {"mime_type": "image/jpeg", "data": img_res.content}])
        else:
            # 텍스트로 보냈을 때 처리 강화
            response = model.generate_content(f"{prompt}\n\n분석할 내용:\n{user_utterance}")

        res_text = response.text.strip()
        info = {"상호": "없음", "대표": "없음", "주소": "없음", "전화": "없음", "팩스": "없음", "이메일": "없음"}
        
        for line in res_text.splitlines():
            clean_line = line.replace('*', '').strip() # 별표 제거
            if ':' in clean_line:
                k, v = clean_line.split(':', 1)
                for key in info:
                    if key in k:
                        val = v.strip()
                        info[key] = format_tel(val) if key in ['전화', '팩스'] else val

        return jsonify({
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": f"📋 분석 결과\n\n상호: {info['상호']}\n대표: {info['대표']}\n주소: {info['주소']}\n전화: {info['전화']}\n팩스: {info['팩스']}\n이메일: {info['이메일']}"}}],
                "quickReplies": [{
                    "label": "📁 연락처 파일 만들기", "action": "message", "messageText": "연락처 파일 만들어줘",
                    "extra": {"name": info['대표'], "org": info['상호'], "tel": info['전화'], "email": info['이메일'], "addr": info['주소'], "fax": info['팩스']}
                }]
            }
        })
    except Exception as e:
        return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "분석에 실패했습니다. 내용을 다시 확인해주세요."}}]}})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
