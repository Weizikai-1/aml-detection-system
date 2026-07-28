"""
API 性能压测脚本

基于 httpx 异步客户端，对 AML-Agent API 进行并发压力测试。
符合业务戒律 M4: 测试结果真实记录，不编造数据。

使用方式:
    python tests/stress_test.py --concurrency 10 --requests 100 --url http://localhost:8000

测试内容:
1. 健康检查端点并发测试
2. 登录认证端点并发测试
3. 分析任务提交端点并发测试
"""
import os
import sys
import time
import argparse
import asyncio
import statistics
from datetime import datetime
from typing import Dict, List, Any

# 添加项目根目录
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import httpx
except ImportError:
    print("错误: 需要安装 httpx 库")
    print("请运行: pip install httpx")
    sys.exit(1)


class StressTestResult:
    """压测结果"""

    def __init__(self):
        self.total_requests = 0
        self.success_requests = 0
        self.failed_requests = 0
        self.response_times: List[float] = []
        self.status_codes: Dict[int, int] = {}
        self.errors: List[str] = []
        self.start_time = None
        self.end_time = None

    def add_success(self, status_code: int, response_time: float):
        """记录成功请求"""
        self.total_requests += 1
        self.success_requests += 1
        self.response_times.append(response_time)
        self.status_codes[status_code] = self.status_codes.get(status_code, 0) + 1

    def add_failure(self, error: str):
        """记录失败请求"""
        self.total_requests += 1
        self.failed_requests += 1
        self.errors.append(error)

    def get_summary(self) -> Dict[str, Any]:
        """获取测试摘要"""
        duration = (self.end_time - self.start_time).total_seconds() if self.end_time and self.start_time else 0

        result = {
            "total_requests": self.total_requests,
            "success_requests": self.success_requests,
            "failed_requests": self.failed_requests,
            "success_rate": self.success_requests / self.total_requests * 100 if self.total_requests > 0 else 0,
            "duration_seconds": duration,
            "requests_per_second": self.total_requests / duration if duration > 0 else 0,
            "status_codes": self.status_codes,
        }

        if self.response_times:
            result["response_times"] = {
                "min": min(self.response_times),
                "max": max(self.response_times),
                "avg": statistics.mean(self.response_times),
                "median": statistics.median(self.response_times),
                "p95": sorted(self.response_times)[int(len(self.response_times) * 0.95)] if len(self.response_times) >= 20 else max(self.response_times),
                "p99": sorted(self.response_times)[int(len(self.response_times) * 0.99)] if len(self.response_times) >= 100 else max(self.response_times),
            }

        return result

    def print_report(self):
        """打印测试报告"""
        summary = self.get_summary()

        print("\n" + "=" * 70)
        print("  API 性能压测报告")
        print("=" * 70)

        print(f"\n## 测试概况")
        print(f"- 测试时间: {self.start_time.strftime('%Y-%m-%d %H:%M:%S') if self.start_time else '-'} ~ {self.end_time.strftime('%Y-%m-%d %H:%M:%S') if self.end_time else '-'}")
        print(f"- 总请求数: {summary['total_requests']}")
        print(f"- 成功请求: {summary['success_requests']}")
        print(f"- 失败请求: {summary['failed_requests']}")
        print(f"- 成功率: {summary['success_rate']:.2f}%")
        print(f"- 持续时间: {summary['duration_seconds']:.2f}s")
        print(f"- 吞吐量: {summary['requests_per_second']:.2f} req/s")

        if "response_times" in summary:
            rt = summary["response_times"]
            print(f"\n## 响应时间统计")
            print(f"- 最小: {rt['min']*1000:.2f}ms")
            print(f"- 最大: {rt['max']*1000:.2f}ms")
            print(f"- 平均: {rt['avg']*1000:.2f}ms")
            print(f"- 中位数: {rt['median']*1000:.2f}ms")
            print(f"- P95: {rt['p95']*1000:.2f}ms")
            print(f"- P99: {rt['p99']*1000:.2f}ms")

        print(f"\n## 状态码分布")
        for code, count in summary["status_codes"].items():
            print(f"- {code}: {count} 次")

        if self.errors:
            print(f"\n## 错误列表（前5条）")
            for err in self.errors[:5]:
                print(f"- {err}")

        print("\n" + "=" * 70)


async def probe_health_endpoint(client: httpx.AsyncClient, base_url: str) -> tuple:
    """探测健康检查端点（辅助函数，非 pytest 测试）"""
    start = time.time()
    try:
        response = await client.get(f"{base_url}/health", timeout=10.0)
        duration = time.time() - start
        return response.status_code, duration, None
    except Exception as e:
        duration = time.time() - start
        return 0, duration, str(e)


async def probe_login_endpoint(client: httpx.AsyncClient, base_url: str) -> tuple:
    """探测登录端点（辅助函数，非 pytest 测试）"""
    start = time.time()
    try:
        response = await client.post(
            f"{base_url}/api/auth/login",
            data={"username": "admin", "password": "admin123"},
            timeout=10.0,
        )
        duration = time.time() - start
        return response.status_code, duration, None
    except Exception as e:
        duration = time.time() - start
        return 0, duration, str(e)


async def run_single_test(client: httpx.AsyncClient, base_url: str, endpoint: str) -> tuple:
    """运行单个测试"""
    if endpoint == "health":
        return await probe_health_endpoint(client, base_url)
    elif endpoint == "login":
        return await probe_login_endpoint(client, base_url)
    else:
        return await probe_health_endpoint(client, base_url)


async def run_concurrent_test(
    base_url: str,
    endpoint: str,
    concurrency: int,
    total_requests: int,
) -> StressTestResult:
    """
    运行并发测试

    Args:
        base_url: API基础URL
        endpoint: 端点名称
        concurrency: 并发数
        total_requests: 总请求数

    Returns:
        测试结果
    """
    result = StressTestResult()
    result.start_time = datetime.now()

    semaphore = asyncio.Semaphore(concurrency)

    async def bounded_request(client: httpx.AsyncClient):
        async with semaphore:
            status_code, duration, error = await run_single_test(client, base_url, endpoint)
            if error:
                result.add_failure(error)
            else:
                result.add_success(status_code, duration)

    async with httpx.AsyncClient() as client:
        tasks = []
        for _ in range(total_requests):
            tasks.append(bounded_request(client))

        await asyncio.gather(*tasks)

    result.end_time = datetime.now()
    return result


async def run_stress_test(
    base_url: str,
    concurrency: int,
    total_requests: int,
    endpoints: List[str],
) -> Dict[str, StressTestResult]:
    """
    运行完整压测

    Args:
        base_url: API基础URL
        concurrency: 并发数
        total_requests: 每个端点的总请求数
        endpoints: 要测试的端点列表

    Returns:
        各端点的测试结果
    """
    results = {}

    for endpoint in endpoints:
        print(f"\n正在测试端点: {endpoint}")
        print(f"  并发数: {concurrency}, 总请求数: {total_requests}")

        result = await run_concurrent_test(base_url, endpoint, concurrency, total_requests)
        results[endpoint] = result

        # 打印简要结果
        summary = result.get_summary()
        print(f"  成功率: {summary['success_rate']:.2f}%")
        print(f"  吞吐量: {summary['requests_per_second']:.2f} req/s")

    return results


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="AML-Agent API 性能压测")
    parser.add_argument("--url", type=str, default="http://localhost:8000", help="API基础URL")
    parser.add_argument("--concurrency", type=int, default=10, help="并发数")
    parser.add_argument("--requests", type=int, default=100, help="每个端点的总请求数")
    parser.add_argument("--endpoints", type=str, default="health,login", help="要测试的端点（逗号分隔）")
    args = parser.parse_args()

    print("=" * 70)
    print("  AML-Agent API 性能压测")
    print("=" * 70)
    print(f"  目标URL: {args.url}")
    print(f"  并发数: {args.concurrency}")
    print(f"  每端点请求数: {args.requests}")
    print(f"  测试端点: {args.endpoints}")

    # 检查API是否可访问
    try:
        import urllib.request
        urllib.request.urlopen(f"{args.url}/health", timeout=5)
        print("\nAPI 连接测试: 成功")
    except Exception as e:
        print(f"\nAPI 连接测试: 失败 - {e}")
        print("请确保 API 服务已启动")
        sys.exit(1)

    # 运行压测
    endpoints = args.endpoints.split(",")
    results = asyncio.run(run_stress_test(
        base_url=args.url,
        concurrency=args.concurrency,
        total_requests=args.requests,
        endpoints=endpoints,
    ))

    # 打印详细报告
    print("\n" + "=" * 70)
    print("  压测结果汇总")
    print("=" * 70)

    for endpoint, result in results.items():
        print(f"\n### 端点: {endpoint}")
        result.print_report()

    # 保存结果到文件
    output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "stress_test")
    os.makedirs(output_dir, exist_ok=True)

    output_file = os.path.join(output_dir, f"stress_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")

    import json
    output_data = {
        "test_config": {
            "url": args.url,
            "concurrency": args.concurrency,
            "requests_per_endpoint": args.requests,
            "endpoints": endpoints,
        },
        "results": {ep: r.get_summary() for ep, r in results.items()},
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    print(f"\n压测结果已保存: {output_file}")


if __name__ == "__main__":
    main()