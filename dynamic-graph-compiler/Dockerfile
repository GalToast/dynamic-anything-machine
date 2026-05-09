FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (better layer caching)
COPY web-demo/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire project so showcase/, work_ledger.py, packet_runtime.py etc. are available
COPY . .

# Create runs directory for the ledger
RUN mkdir -p runs/demo

# Environment variables
ENV PORT=5000
ENV FLASK_ENV=production
# ANTHROPIC_AUTH_TOKEN should be set in Railway environment variables

EXPOSE 5000

# Run: cd into web-demo so sys.path.insert(0, '..') resolves to project root
CMD cd web-demo && python app.py
