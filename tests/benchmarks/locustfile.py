"""
Locust 性能压测脚本

符合业务戒律 M4: 测试结果真实记录，不编造数据。

使用方式:
    locust -f tests/locustfile.py --host=http://localhost:8000
    locust -f tests/locustfile.py --host=http://localhost:8000 --headless -u 50 -r 10 -t 60s

测试内容:
1. 健康检查端点测试
2. 登录认证端点测试
3. 分析任务提交端点测试
4. 规则列表查询端点测试
"""
import json
import time
from locust import HttpUser, task, between, TaskSet, events


class AMLAgentTasks(TaskSet):
    """反洗钱系统任务集"""

    def on_start(self):
        """用户初始化"""
        self.token = None
        self.login()

    def login(self):
        """登录获取令牌"""
        response = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "Weizikai0315_"}
        )
        if response.status_code == 200:
            data = response.json()
            self.token = data.get("access_token")
            self.client.headers.update({"Authorization": f"Bearer {self.token}"})

    @task(1)
    def test_health(self):
        """健康检查"""
        self.client.get("/health")

    @task(2)
    def test_analysis_submit(self):
        """提交分析任务"""
        payload = {
            "account_id": f"ACC{int(time.time() % 100000):06d}",
            "transactions": [
                {
                    "transaction_id": f"TXN{int(time.time()):012d}",
                    "amount": 125000.00,
                    "currency": "CNY",
                    "type": "transfer",
                    "counterparty": f"CP{int(time.time() % 1000):04d}",
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")
                }
            ]
        }
        self.client.post("/api/analysis/submit", json=payload)

    @task(2)
    def test_rules_list(self):
        """查询规则列表"""
        self.client.get("/api/rules")

    @task(1)
    def test_alerts_list(self):
        """查询告警列表"""
        self.client.get("/api/alerts")

    @task(1)
    def test_audit_logs(self):
        """查询审计日志"""
        self.client.get("/api/audit/logs")


class AMLAgentUser(HttpUser):
    """反洗钱系统用户"""
    tasks = [AMLAgentTasks]
    wait_time = between(1, 3)
    host = "http://localhost:8000"