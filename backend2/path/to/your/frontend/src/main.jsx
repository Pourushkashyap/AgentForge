import React from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App.jsx';
import './styles.css';

class AppErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error('[AgentForge] render crash:', error, info && info.componentStack);
  }

  render() {
    if (this.state.error) {
      const message =
        (this.state.error && this.state.error.message) || String(this.state.error);
      return (
        <div
          data-agentforge-crash="true"
          role="alert"
          style={{ padding: '2rem', fontFamily: 'sans-serif' }}
        >
          <h1 style={{ fontSize: '1.25rem', fontWeight: 700 }}>This page crashed</h1>
          <pre style={{ whiteSpace: 'pre-wrap', marginTop: '1rem' }}>{message}</pre>
        </div>
      );
    }
    return this.props.children;
  }
}

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <AppErrorBoundary>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </AppErrorBoundary>
  </React.StrictMode>
);
