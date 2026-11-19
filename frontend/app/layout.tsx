import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: '研辩台 ThesisDebate',
  description: '可追溯的论文评审与格式修订工作台',
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body>{children}</body></html>
}
