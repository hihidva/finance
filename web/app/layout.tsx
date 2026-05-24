import './globals.css';
import type { Metadata } from 'next';
import Link from 'next/link';

export const metadata: Metadata = {
  title: 'finance-bot dashboard',
  description: 'Quản lý watchlist, đồ thị và lịch sử signal của finance-bot.',
};

// Resolved at build time from NEXT_PUBLIC_API_BASE (defaults to :4030; :5030 in test mode).
const apiBaseLabel = (process.env.NEXT_PUBLIC_API_BASE ?? 'http://127.0.0.1:4030')
  .replace(/^https?:\/\//, '');

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="vi">
      <body>
        <div className="min-h-screen flex flex-col">
          <header className="border-b border-slate-200 bg-white">
            <div className="max-w-7xl mx-auto px-4 h-14 flex items-center justify-between">
              <Link href="/" className="font-semibold text-slate-900">
                finance-bot <span className="text-slate-400">/ dashboard</span>
              </Link>
              <nav className="flex gap-1 text-sm">
                <NavLink href="/watchlist">Watchlist</NavLink>
                <NavLink href="/charts">Đồ thị</NavLink>
                <NavLink href="/signals">Lịch sử signals</NavLink>
              </nav>
            </div>
          </header>
          <main className="flex-1 max-w-7xl mx-auto w-full px-4 py-6">
            {children}
          </main>
          <footer className="border-t border-slate-200 bg-white text-xs text-slate-500">
            <div className="max-w-7xl mx-auto px-4 h-10 flex items-center justify-between">
              <span>Localhost only • Single-user • No auth</span>
              <span>API: {apiBaseLabel}</span>
            </div>
          </footer>
        </div>
      </body>
    </html>
  );
}

function NavLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <Link
      href={href}
      className="px-3 py-1.5 rounded-md hover:bg-slate-100 text-slate-700"
    >
      {children}
    </Link>
  );
}
