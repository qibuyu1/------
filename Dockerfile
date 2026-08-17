FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY . /app
ENV HOST=0.0.0.0 PORT=8787
EXPOSE 8787
CMD ["python", "server.py"]
