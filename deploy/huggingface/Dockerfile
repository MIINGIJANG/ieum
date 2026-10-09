# Hugging Face Spaces (Docker) 용. scripts/deploy/make_space.py 가 이 파일을 배포 폴더로 복사함.
FROM python:3.11-slim
RUN useradd -m -u 1000 user
WORKDIR /app
COPY requirements.txt .
# torch 는 CPU 전용판(용량 1/4)으로 먼저 설치
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir -r requirements.txt
COPY --chown=user . .
USER user
ENV KSL_CKPT=checkpoints/best.pt
EXPOSE 7860
CMD ["python", "-m", "uvicorn", "api:app", "--host", "0.0.0.0", "--port", "7860"]
