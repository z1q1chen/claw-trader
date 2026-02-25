import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Claw Trader",
  description: "Autonomous quant trading agent",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
