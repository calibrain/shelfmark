import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { SocketProvider } from './contexts/SocketContext';
import App from './App';
import { getBasePath } from './utils/basePath';

const root = document.getElementById('root');
if (!root) throw new Error('Root element not found');

const basePath = getBasePath();
const routerBase = basePath === '/' ? undefined : basePath;

createRoot(root).render(
  <StrictMode>
    <BrowserRouter basename={routerBase}>
      <SocketProvider>
        <App />
      </SocketProvider>
    </BrowserRouter>
  </StrictMode>
);
