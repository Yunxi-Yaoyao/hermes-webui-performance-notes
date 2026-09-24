# 排查方法

## 1. 确认查的是哪套服务

本案例对象是第三方`hermes-web-ui` npm包，不是Hermes Agent官方Dashboard。由服务MainPID读取进程cwd、node路径和实际package.json，不能仅看PATH中的另一个npm版本。Linux服务示例：

```bash
systemctl show hermes-web-ui -p MainPID -p ExecStart
ss -ltnp
```

不要将完整环境变量、config或认证文件粘到公开issue。日志也可能包含会话正文。

## 2. 用同一个资源比较本机与公网

从当前HTML读取script src（带hash名称会变）。各条件重复采样，不把并行资源时间相加。

```bash
curl --compressed -sS -o /dev/null \
  -w 'code=%{http_code} dns=%{time_namelookup} tcp=%{time_connect} tls=%{time_appconnect} ttfb=%{time_starttransfer} total=%{time_total}\n' \
  'http://127.0.0.1:PORT/assets/js/CURRENT-ENTRY.js'
```

记录是否gzip/br、压缩传输大小与解压大小。loopback也慢时，先查服务器；公网慢不代表全部是后端。基线入口JS本机约1.95秒。

## 3. 查单线程热点

整机仍有大量空闲CPU不排除Node主线程满载。`ps`长期百分比与/proc两次采样不同；按逻辑核归一的监控值不能与单核百分比混用。

```bash
# PID替换为现场值；采样会有开销，性能计时不要与strace同时进行。
timeout -s INT 5 strace -c -p PID
perf record -F 49 -p PID -g -o perf.data -- sleep 5
DEBUGINFOD_URLS= perf report --stdio --no-children -i perf.data
```

本例调用链：UDPWrap::OnRecv → Promise/microtask → os::GetInterfaceAddresses → getifaddrs → netlink_recvmsg。`recvmsg`多不等于“公网下载慢”，这里是同步枚举本机接口。

## 4. 只抓需要的发现端口

```bash
# 使用本机实际接口与端口。只在授权主机操作，限时抓取。
timeout -s INT 3 tcpdump -i TUN_INTERFACE -nn -s 200 \
  -w discovery.pcap 'udp dst port DISCOVERY_PORT'
```

实测2.870562秒捕获21,476条，约7,481包/秒，同一个request_id。解析pcap后按ID/五元组计数，不靠视觉估计日志行数。别主动制造广播风暴来复现，也不要把原始包直接公开。

## 5. 验证策略路由优先级

```bash
ip rule show
ip route get TUN_BROADCAST
ip route get TUN_BROADCAST mark 1
```

本例两次查询均命中`table local`的TUN广播路由。priority 0的local表在fwmark规则前执行，出站即使设置mark也仍回到TUN。因此“有mark就一定防环”不成立。

上游扫描将虚拟接口广播地址纳入候选，透明代理再次发送，包又命中TUN广播，循环维持。WebUI收到重复发现请求后反复枚举接口。精确drop一个循环包后高负载消失，强化了抓包与CPU栈之间的因果证据。

规则不是普遍修复：不同TUN后端、路由表、协议和发现算法不一定复现。需要同时证据，不仅凭进程名和高CPU套用。

## 6. 登录态冷/热缓存与消息恢复

- 冷：新的浏览器上下文，只有授权登录状态，没有HTTP缓存。
- 热：同上下文真正`page.reload()`；重复goto相同hash可能没有导航。
- `window.load`可能早于SPA会话渲染，也可能被非关键图片拖长。
- 首屏判定包含侧栏、消息正文Markdown、输入框、当前视口有效图片、背景decode。
- 背景命中CacheStorage时没有fetch，不能误判“背景未下载就没加载”。
- WS只记录事件名、到达时间与帧大小，不保存消息payload或token。
- 虚拟列表不会绘制全部历史；当前首屏完整不等于全历史都加载。

遇到CSP时不关闭网站安全策略；工具的wait_for_function字符串eval可能失败，用locator状态或普通evaluate的函数形式。测试出现版本公告是业务弹窗，不等于网络未完成；可见输入框也不等于已关闭弹窗后可键入。

## 7. 别把实验异常当成功数据

案例中曾修正：相同hash goto不刷新、缓存背景未fetch误判、后台Python环境不同。无效样本单独标记，不参与“变快多少”。公网502/超时单列失败，不混入成功平均值。

后续成对health探测发现本机约0.047～0.048秒、公网约0.38～0.54秒；另有整页多个接口/WS一起等待数秒。没有远端日志时只能定位到额外路径等待，不能断言具体哪一跳。
