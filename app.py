import os
import uuid
import re
import requests
import threading
import json
import time
import gspread  # 🚀 추가
from oauth2client.service_account import ServiceAccountCredentials # 🚀 추가
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from google import genai
from google.genai import types

app = Flask(__name__)

# 경로 및 환경 변수 설정
STATIC_DIR = '/tmp/static'
os.makedirs(STATIC_DIR, exist_ok=True)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT")

# 🚀 구글 시트 연동 함수
def append_to_sheet(info):
    if not SHEET_ID or not SERVICE_ACCOUNT_JSON:
        print("구글 시트 설정이 누락되었습니다.")
        return False
    
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds_dict = json.loads(SERVICE_ACCOUNT_JSON)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(SHEET_ID).sheet1

        # 🚀 중복 체크: [대표 + 상호] 조합이 이미 있는지 확인
        existing_data = sh.get_all_values()
        name_org_pair = f"{info.get('대표','')}_{info.get('상호','')}"
        
        for row in existing_data:
            if len(row) >= 3:
                # 시트의 2열(대표), 1열(상호) 데이터와 비교
                if row[1] == info.get('대표') and row[0] == info.get('상호'):
                    print("중복 데이터 발견: 저장을 건너뜁니다.")
                    return "DUPLICATE"

        # 데이터 행 구성 [상호, 대표, 직급, 전화, 이메일, 주소, 분석일시]
        new_row = [
            info.get('상호', '없음'),
            info.get('대표', '없음'),
            info.get('직급', '없음'),
            info.get('전화', '없음'),
            info.get('이메일', '없음'),
            info.get('주소', '없음'),
            datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        ]
        sh.append_row(new_row)
        return True
    except Exception as e:
        print(f"시트 저장 에러: {e}")
        return False

# ... (중간 format_tel, clean_org_name 등 ver 1 함수들은 그대로 유지) ...

def create_res_template(info, sheet_status=None):
    lines = [
        "📋 명함 분석 결과", "━━━━━━━━━━━━━━",
        f"🏢 상호: {info.get('상호', '없음')}",
        f"👤 대표: {info.get('대표', '없음')}",
        f"🎖️ 직급: {info.get('직급', '없음')}",
        f"📍 주소: {info.get('주소', '없음')}",
        f"📞 전화: {format_tel(info.get('전화', '없음'))}",
        f"📧 메일: {info.get('이메일', '없음')}"
    ]
    
    # 시트 저장 상태 메시지 추가
    if sheet_status == "DUPLICATE":
        lines.append("\n⚠️ 이미 시트에 존재하는 정보입니다.")
    elif sheet_status is True:
        lines.append("\n✅ 구글 시트에 저장 완료!")
    
    lines.append("━━━━━━━━━━━━━━")
    
    return {
        "version": "2.0",
        "template": {
            "outputs": [{"simpleText": {"text": "\n".join(lines)}}],
            "quickReplies": [{
                "label": "📁 연락처 파일 만들기",
                "action": "message",
                "messageText": "연락처 파일 만들어줘",
                "extra": info
            }]
        }
    }

# 🚀 분석 실행 및 시트 저장 로직 통합
def run_full_process(client, user_text, image_url):
    info = run_analysis(client, user_text, image_url)
    if isinstance(info, dict) and info.get("대표") != "재시도필요":
        # 분석이 성공하면 즉시 구글 시트에 업로드 시도
        status = append_to_sheet(info)
        return info, status
    return info, None

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
        user_text = params.get('user_input') or data.get('userRequest', {}).get('utterance', '')

        # 1. 연락처 파일 생성 로직 (기존 ver 1 동일)
        if client_extra:
            # ... (기존 VCF 생성 코드 생략) ...
            return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "연락처 생성 완료"}}]}})

        # 2. 메인 분석 및 시트 저장 로직
        state = {"info": None, "sheet_status": None, "is_timeout": False}
        
        def worker():
            info, status = run_full_process(client, user_text, image_url)
            state["info"] = info
            state["sheet_status"] = status
            if state["is_timeout"] and callback_url:
                res = create_res_template(state["info"], state["sheet_status"])
                requests.post(callback_url, json=res, timeout=15)
        
        t = threading.Thread(target=worker); t.start(); t.join(timeout=3.5)

        if state["info"]:
            return jsonify(create_res_template(state["info"], state["sheet_status"]))
        
        state["is_timeout"] = True
        return jsonify({"version": "2.0", "useCallback": True, "data": {"text": "명함 분석 및 엑셀 저장 중... ⏳"}})
    except:
        return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "시스템 오류"}}]}})

# ... (이하 동일) ...
