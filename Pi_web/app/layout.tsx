import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "外墙检测机器人控制台",
  description: "双螺旋桨风机与敲击装置实时控制台",
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body className="antialiased">{children}</body>
    </html>
  );
}
