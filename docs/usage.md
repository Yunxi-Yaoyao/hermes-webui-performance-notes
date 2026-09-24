# 操作、风险与回滚

先读[诊断](diagnosis.md)。这些工具不修改数据库、不重启服务、不替你配置透明代理。任何生产变更前保留包版本、文件摘要和独立备份；不要在npm升级运行时同时修改文件。

## A. 精确发现广播保护（Linux）

前提：抓包已证实相同发现请求进入TUN循环，且带mark的route get仍落在TUN广播。正常的重复扫描不是充分证据。

使用实际子网替换文档测试网段。默认show不改规则：

```bash
python3 scripts/discovery_guard.py --action show \
  --interface tun-test --subnet 198.51.100.0/30 --port 52008
```

确认后才显式修改（需要root）：

```bash
sudo python3 scripts/discovery_guard.py --action apply --confirm \
  --interface tun-test --subnet 198.51.100.0/30 --port 52008
```

apply检查接口的真实IPv4子网与参数一致；在OUTPUT末尾追加规则，仅针对该接口、源子网、广播目的地址和UDP端口drop。公开工具比现场规则多了源子网限制，并使用追加而非前插：如果前面已有匹配的ACCEPT，或代理用不同源地址发包，这条规则可能不命中。须检查计数与抓包，不把“规则存在”当“循环已解除”；不要自动改动整个防火墙顺序。不会处理整个私网、清空防火墙或停止代理。防误用限制仅接受tun/tap类名称及合理子网，无法覆盖所有合法但不同命名的VPN环境；不符合就人工复核，不直接放宽。

验证：重复包消失、Node CPU下降、本机静态资源恢复、真实LAN设备仍可发现。向本机LAN地址探测成功，只证明响应器保留，不等于跨设备扫描完整验收。

回滚只移除相同规则：

```bash
sudo python3 scripts/discovery_guard.py --action remove --confirm \
  --interface tun-test --subnet 198.51.100.0/30 --port 52008
```

本工具不持久化。确定后可用自己的oneshot/systemd网络管理接入，每次启动重新检查参数。撤销后循环可能复发。**不要用整个iptables-save备份覆盖正在变化的容器/k8s规则。**

## B. 合并初始化GET（只支持已验0.7.22文件）

`patches/singleflight.js`精确白名单：

- `/api/hermes/profiles`
- `/api/auth/me`
- `/api/hermes/config`

同源、GET、完整URL/全部headers/选项都相同才共享在飞请求。Authorization与X-Hermes-Profile进入key；带signal或Request对象绕过；响应给每个调用方独立clone。响应完成立即清理，不保留TTL缓存。任何非GET开始与结束都清理；非GET进行期间GET不合并，避免保存配置期间的旧读污染保存后新读。

这不是认证层。仍由原服务鉴权。Cookie切换、特殊认证网关或不同客户端封装要重新评估；本案使用显式Bearer头。给配置请求缓存几分钟不是等价替代。

```bash
# 检查：缺补丁/未知版本返回非零；不会写包
python3 scripts/frontend_overlay.py check \
  --root /path/to/hermes-web-ui \
  --backup-dir /path/outside-package/webui-overlay-backup

# 应用：需要写包和备份目录的权限，只用于已验版本
python3 scripts/frontend_overlay.py apply --confirm \
  --root /path/to/hermes-web-ui \
  --backup-dir /path/outside-package/webui-overlay-backup

# 撤销：先核对receipt与当前hash，陌生更改拒绝覆盖
python3 scripts/frontend_overlay.py remove --confirm \
  --root /path/to/hermes-web-ui \
  --backup-dir /path/outside-package/webui-overlay-backup
```

脚本前置于入口module，新标签或刷新生效；不动原hash JS，不需要改服务代码。不要只看脚本文件已复制就声称成功：浏览器检查API次数、鉴权错误处理、头像/正文、控制台错误及更新设置后的正确性。

## C. 图标尺寸（保留原格式/URL的保守方式）

案例中的三个1254px PNG在UI只显示几十像素，缩到256px后视觉对照无明显损失。但这里不分发上游品牌图或用户头像，也不代替审美验收。

先在**独立staging目录**生成，确认原图已备份，禁止原地批量改未知图片。示例：

```python
from PIL import Image
image = Image.open('original-icon.png').convert('RGBA')
image.thumbnail((256, 256), Image.Resampling.LANCZOS)
image.save('staged-icon.png', format='PNG', optimize=True)
```

Pillow单独安装在venv。检查透明边缘、真实显示尺寸和高DPI。比对通过后才原子替换指定文件；HTTP回读内容与staged比对，回滚使用同版本备份。该方式减少每次下载量，**不等于实现长缓存**，升级可能覆盖文件。

## D. 聊天预加载（输出供审阅，不自动安装）

```bash
python3 scripts/preload.py \
  --client-root /path/to/hermes-web-ui/dist/client > /tmp/chat-preload.html
```

只有0.7.22布局允许；缺文件/匹配不明确直接报错。生成内容包含静态modulepreload和CSS preload，只在chat/session hash路由执行，不改权限，不执行业务模块。将审阅后的单个marker块放入当前HTML的head，回滚只删除该块。不要把预加载当成“少传代码”，它只改变时机；弱网过度并发可能争用带宽，需要A/B。

**本仓库前端安装器仅安装GET合并，不自动安装预加载或缩略图。** 手工预加载插入后会改变HTML摘要，原overlay receipt可能因此拒绝回滚；先撤销后加的预加载，核对摘要，再撤销overlay。不能绕过摘要保护强制恢复旧HTML。

## E. 每次验收

1. 文件/配置：备份、目标版本、修改范围与回滚路径明确。
2. 运行：服务活着、HTTP/WS正常、CPU没有新异常。
3. 登录态：当前会话可读、输入框/头像/正文正确；不为了测速发送聊天消息。
4. 数据：不修改/重置用户数据；若升级涉及SQLite，采用在线backup而非直接cp活库。
5. 性能：同设备同路由，冷/热分别测，保留所有慢样本与失败样本。
6. 更新：新版未经验证就拒绝旧补丁，保留原版可启动；不要扩大版本号名单假装兼容。
