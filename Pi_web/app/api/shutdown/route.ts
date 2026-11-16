// 安全停止的网页侧兜底：收到请求后先响应，再退出进程。
// 注意：vinext dev 的路由运行在 workerd 里，process.exit(0) 可能是空操作；
// 树莓派部署下真正停掉本服务的是 camera_service.py 的 /safe-stop
// （免密 sudo systemctl stop robot-web），本路由只是备用路径。

export const dynamic = "force-dynamic";

export async function POST() {
  setTimeout(() => {
    console.log("[shutdown] 网页服务收到安全停止，正在退出（等效 Ctrl+C）……");
    process.exit(0);
  }, 300);
  return Response.json({ ok: true, message: "网页服务正在关闭" });
}
