import type { Metadata } from "next";
import "./globals.css";
import { ThemeProvider } from "@/components/theme-provider";
import { AppSidebar } from "@/components/app-sidebar";

export const metadata: Metadata = {
  title: "segment-compare · Dashboard",
  description: "Visual dashboard UI for the segment-compare engine.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <ThemeProvider>
          <div className="flex">
            <AppSidebar />
            <main className="h-screen flex-1 overflow-y-auto">
              <div className="mx-auto max-w-6xl px-6 py-6">{children}</div>
            </main>
          </div>
        </ThemeProvider>
      </body>
    </html>
  );
}
