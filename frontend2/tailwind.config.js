export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      // Palette: near-black violet + purple + white. No other hues.
      colors: {
        ink: '#0B0A12',      // app background
        steel: '#13111D',    // panels
        plate: '#1B1828',    // nodes, chips, raised surfaces
        seam: '#2D2942',     // borders and dividers
        fog: '#F5F3FF',      // primary text
        mist: '#A59CC8',     // secondary text (violet tint)
        brand: { DEFAULT: '#8B5CF6', hot: '#7C3AED', deep: '#6D28D9', light: '#C4B5FD' },
        arc: '#A78BFA',      // in progress
        ok: '#C4B5FD',       // done
        warn: '#DDD6FE',     // warnings
        bad: '#F0ABFC',      // errors (light fuchsia, still in the purple family)
      },
      fontFamily: {
        display: ['"Bricolage Grotesque"', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        sans: ['"Hanken Grotesk"', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      boxShadow: { glow: '0 0 22px rgba(139,92,246,0.40)' },
      keyframes: {
        forge: { '0%': { left: '-30%' }, '100%': { left: '100%' } },
        rise: { '0%': { opacity: 0, transform: 'translate(-50%, 8px)' }, '100%': { opacity: 1, transform: 'translate(-50%, 0)' } },
      },
      animation: { forge: 'forge 2.2s linear infinite', rise: 'rise .2s ease-out' },
    },
  },
  plugins: [],
}
