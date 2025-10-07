#!/bin/bash
# Container diagnostics script for troubleshooting

echo "=== Container Diagnostics ==="
echo "Timestamp: $(date)"
echo

echo "=== Docker Container Status ==="
docker ps -a --filter "name=personal-tracking"
echo

echo "=== Container Health Check ==="
docker inspect personal-tracking --format='{{.State.Health.Status}}'
echo

echo "=== Container Resource Usage ==="
docker stats personal-tracking --no-stream
echo

echo "=== Recent Container Logs ==="
docker logs personal-tracking --tail 50
echo

echo "=== Application Health Check ==="
curl -s http://localhost:8000/health | jq . 2>/dev/null || echo "Health endpoint not responding"
echo

echo "=== Container Environment ==="
docker exec personal-tracking env | grep -E "(DATA_DIR|PYTHONUNBUFFERED|TZ)"
echo

echo "=== Database File Status ==="
docker exec personal-tracking ls -la /data/
echo

echo "=== Disk Space in Container ==="
docker exec personal-tracking df -h
echo

echo "=== Memory Usage in Container ==="
docker exec personal-tracking free -h
echo

echo "=== Process List in Container ==="
docker exec personal-tracking ps aux
echo

echo "=== Network Connectivity Test ==="
docker exec personal-tracking wget -qO- http://localhost:8000/health 2>/dev/null || echo "Internal health check failed"
echo

echo "=== Container Exit Codes ==="
docker inspect personal-tracking --format='{{.State.ExitCode}}'