import "./globals.css";
import type { ReactNode } from "react";

export const metadata = {
  title: "엔젤토글 판매관리",
  description: "엔젤토글 라이선스 판매관리 어드민",
};

export default function RootLayout({children}:{children:ReactNode}) {
  return <html lang="ko"><body>{children}</body></html>;
}
