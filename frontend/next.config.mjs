const isProd = process.env.NODE_ENV === 'production'
const internalHost = process.env.TAURI_DEV_HOST || 'localhost'
const devPort = process.env.PORT || '3000'

const nextConfig = {
  output: 'export',
  images: { unoptimized: true },
  assetPrefix: isProd ? undefined : 'http://' + internalHost + ':' + devPort,
}

export default nextConfig
