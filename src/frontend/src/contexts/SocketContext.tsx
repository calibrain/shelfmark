import type { ReactNode } from 'react';
import { createContext, useContext, useEffect, useMemo, useState } from 'react';
import type { Socket } from 'socket.io-client';
import { io } from 'socket.io-client';

import { withBasePath } from '../utils/basePath';

interface SocketContextValue {
  socket: Socket | null;
  connected: boolean;
}

const SocketContext = createContext<SocketContextValue>({ socket: null, connected: false });

export const useSocket = () => useContext(SocketContext);

interface SocketProviderProps {
  children: ReactNode;
}

export const SocketProvider = ({ children }: SocketProviderProps) => {
  const [connected, setConnected] = useState(false);
  const [socket, setSocket] = useState<Socket | null>(null);

  useEffect(() => {
    // Always connect via current origin so dev proxy and session cookies stay aligned.
    const wsUrl = window.location.origin;
    const socketPath = withBasePath('/socket.io');

    console.log('SocketProvider: Connecting to', wsUrl);

    const nextSocket = io(wsUrl, {
      path: socketPath,
      transports: ['polling', 'websocket'],
      withCredentials: true,
    });

    setSocket(nextSocket);

    nextSocket.on('connect', () => {
      console.log('✅ Socket connected via', nextSocket.io.engine.transport.name);
      setConnected(true);
    });

    nextSocket.on('disconnect', (reason) => {
      console.log('Socket disconnected:', reason);
      setConnected(false);
    });

    nextSocket.on('connect_error', (err) => {
      console.error('Socket connection error:', err.message);
      setConnected(false);
    });

    return () => {
      console.log('SocketProvider: Disconnecting');
      nextSocket.disconnect();
    };
  }, []);

  const contextValue = useMemo(() => ({ socket, connected }), [socket, connected]);

  return <SocketContext.Provider value={contextValue}>{children}</SocketContext.Provider>;
};
