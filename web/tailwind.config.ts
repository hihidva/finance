import type { Config } from 'tailwindcss';

const config: Config = {
  content: [
    './app/**/*.{ts,tsx}',
    './components/**/*.{ts,tsx}',
    './lib/**/*.{ts,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        // Asset-class badge palette (mirrors business doc §7.4.1)
        ac: {
          vn_stock: '#16a34a',
          crypto: '#ea580c',
          commodity: '#ca8a04',
          fx_index: '#64748b',
        },
        // Tier palette
        tier: {
          A: '#15803d',
          B: '#a16207',
          C: '#52525b',
        },
        side: {
          buy: '#16a34a',
          sell: '#dc2626',
          hold: '#64748b',
        },
      },
    },
  },
  plugins: [],
};

export default config;
