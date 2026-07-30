"""手动端到端检查：连接 XUSE 仿真服务器、触发四类握手、读写节点。"""
import argparse
import time
import sys
from opcua import Client, ua

URL = "opc.tcp://127.0.0.1:4855/xuse_sim/"
NS_INDEX = 4

def nid(cn: str) -> str:
    return f"ns={NS_INDEX};s=uniab|{cn}"

def wait_true(client, cn, timeout=5.0):
    node = client.get_node(nid(cn))
    t0 = time.time()
    while time.time() - t0 < timeout:
        if node.get_value() is True:
            return True
        time.sleep(0.05)
    return False

def wait_false(client, cn, timeout=5.0):
    node = client.get_node(nid(cn))
    t0 = time.time()
    while time.time() - t0 < timeout:
        if node.get_value() is False:
            return True
        time.sleep(0.05)
    return False

def main(argv=None):
    global URL, NS_INDEX
    parser = argparse.ArgumentParser(description="OpcUaSim 四类握手端到端测试")
    parser.add_argument("--url", default=URL)
    parser.add_argument("--ns-index", type=int, default=NS_INDEX)
    args = parser.parse_args(argv)
    URL = args.url
    NS_INDEX = args.ns_index
    all_ok = True

    print(f"[client] connecting to {URL}")
    client = Client(URL)
    client.connect()
    print("[client] connected")

    try:
        # ---- 1) 基础读取：读几个已知节点 ----
        for name in ["工站初始化", "工站初始化完成", "机械臂空闲_1", "加珠占位"]:
            v = client.get_node(nid(name)).get_value()
            print(f"  read {name} = {v}")

        # ---- 2) Type-D 初始化握手：工站初始化 ----
        print("\n[T-D] 工站初始化：写 True → 等 '工站初始化完成'=True → 写 False → 等清零")
        client.get_node(nid("工站初始化")).set_value(True)
        ok = wait_true(client, "工站初始化完成", timeout=3.0)
        print(f"  '工站初始化完成' → True? {ok}")
        client.get_node(nid("工站初始化")).set_value(False)
        ok2 = wait_false(client, "工站初始化完成", timeout=3.0)
        print(f"  '工站初始化完成' → False? {ok2}")
        all_ok = all_ok and ok and ok2

        # ---- 3) Type-C 参数下发（若存在此类节点）----
        # 查找一个带 "参数下发" 后缀的写节点
        param_pairs = [
            ("加样参数下发", "加样参数下发完成"),
            ("加珠参数下发", "加珠参数下发完成"),
        ]
        for w, r in param_pairs:
            try:
                client.get_node(nid(w)).get_value()  # 探测存在
            except Exception:
                continue
            print(f"\n[T-C] 参数下发：{w}")
            client.get_node(nid(w)).set_value(True)
            ok = wait_true(client, r, timeout=2.0)
            print(f"  '{r}' → True? {ok}")
            client.get_node(nid(w)).set_value(False)
            ok2 = wait_false(client, r, timeout=2.0)
            print(f"  '{r}' → False? {ok2}")
            all_ok = all_ok and ok and ok2
            break

        # ---- 4) Type-A 编码触发：机械臂动作 ----
        print("\n[T-A] 机械臂动作触发_1：目标位置=42 → 触发 → 完成后当前位置=42")
        try:
            client.get_node(nid("机械臂目标位置代码_1")).set_value(ua.Variant(42, ua.VariantType.Int16))
            print(f"  写目标位置=42 OK")
            target_write_ok = True
        except Exception as e:
            print(f"  写目标位置失败: {e}")
            target_write_ok = False

        client.get_node(nid("机械臂动作触发_1")).set_value(True)
        ok = wait_true(client, "机械臂动作完成_1", timeout=5.0)
        cur = client.get_node(nid("机械臂当前位置_1")).get_value()
        print(f"  '机械臂动作完成_1' → True? {ok}, '机械臂当前位置_1' = {cur}")
        client.get_node(nid("机械臂动作触发_1")).set_value(False)
        ok2 = wait_false(client, "机械臂动作完成_1", timeout=2.0)
        print(f"  '机械臂动作完成_1' → False? {ok2}")
        all_ok = all_ok and target_write_ok and ok and cur == 42 and ok2

        # ---- 5) Type-B 请求-应答：加珠 (或存在的任意 process_B) ----
        # 服务器启动时把占位置了 TRUE，请求节点会被引擎在完成后 300ms 内置 TRUE
        print("\n[T-B] 加珠请求加工：等 REQ=True → 写开始加工=True → 等加工完成=True → 撤销")
        req_ok = wait_true(client, "加珠请求加工", timeout=5.0)
        print(f"  '加珠请求加工' → True? {req_ok}")
        if req_ok:
            client.get_node(nid("加珠开始加工")).set_value(True)
            done = wait_true(client, "加珠加工完成", timeout=5.0)
            print(f"  '加珠加工完成' → True? {done}")
            client.get_node(nid("加珠开始加工")).set_value(False)
            done_off = wait_false(client, "加珠加工完成", timeout=3.0)
            print(f"  '加珠加工完成' → False? {done_off}")
            all_ok = all_ok and done and done_off
        else:
            all_ok = False

        if all_ok:
            print("\n[client] PASS：所有握手测试通过")
            return 0
        print("\n[client] FAIL：至少一项握手测试未通过")
        return 1
    finally:
        client.disconnect()
        print("[client] disconnected")

if __name__ == "__main__":
    sys.exit(main() or 0)
