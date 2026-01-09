import os
import requests
from flask import Flask, request, jsonify
from google import genai

app = Flask(__name__)

# 환경변수에서 API 키 가져오기
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

def get_client():
    if not GEMINI_API_KEY:
        return None
    return genai.Client(api_key=GEMINI_API_KEY)

@app.route('/')
def health_check():
    return "OK", 200

@app.route('/api/get_biz_info', methods=['POST'])
@app.route('/api/get_biz_info/', methods=['POST'])
def get_biz_info():
    try:
        client = get_client()
        if not client:
            return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "API 키가 설정되지 않았습니다."}}]}})

        # [디버깅] 현재 API 키로 사용 가능한 모든 모델 목록 가져오기
        model_names = []
        try:
            for m in client.models.list():
                # 'models/' 접두사를 떼고 이름만 저장
                name = m.name.replace('models/', '')
                model_names.append(name)
        except Exception as list_err:
            return jsonify({"version": "2.0", "template": {"outputs": [{"simpleText": {"text": f"목록 가져오기 실패: {str(list_err)}"}}]}})

        # 모델 목록을 줄바꿈으로 합쳐서 응답
        if not model_names:
            response_text = "사용 가능한 모델이 없습니다."
        else:
            response_text = "🔍 사용 가능한 모델 목록:\n\n" + "\n".join(model_names)

        return jsonify({
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": response_text}}]
            }
        })

    except Exception as e:
        return jsonify({
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": f"서버 오류 발생: {str(e)}"}}]
            }
        })

if __name__ == '__main__':
    # Render 등 호스팅 환경의 포트 설정 준수
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
