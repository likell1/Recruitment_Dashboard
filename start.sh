#!/bin/bash
cd "$(dirname "$0")"

if [ ! -f config.yaml ]; then
    echo "⚠️  config.yaml이 없습니다."
    cp config.yaml.example config.yaml
    echo "✅ config.yaml을 생성했습니다. 파일을 열어 DB 접속 정보를 입력 후 다시 실행하세요."
    open config.yaml 2>/dev/null || nano config.yaml
    exit 1
fi

echo "📦 패키지 설치 중..."
pip install -r requirements.txt -q

echo "🚀 대시보드 시작: http://localhost:5050"
python app.py
