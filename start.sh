#!/bin/bash
cd "$(dirname "$0")"

if [ ! -f config.yaml ]; then
    echo "config.yaml이 없습니다."
    cp config.yaml.example config.yaml
    echo "config.yaml을 생성했습니다. DB 접속 정보를 입력 후 다시 실행하세요."
    nano config.yaml
    exit 1
fi

if [ ! -d .venv ]; then
    echo "가상환경 생성 중..."
    python3 -m venv .venv
fi

echo "패키지 설치 중..."
.venv/bin/python -m pip install -r requirements.txt -q

echo "대시보드 시작: http://localhost:5050"
.venv/bin/python app.py
