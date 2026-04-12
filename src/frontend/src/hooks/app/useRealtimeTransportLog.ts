import { useEffect } from 'react';

export const useRealtimeTransportLog = (isUsingWebSocket: boolean): void => {
  useEffect(() => {
    if (isUsingWebSocket) {
      console.log('✅ Using WebSocket for real-time updates');
    } else {
      console.log('⏳ Using polling fallback (5s interval)');
    }
  }, [isUsingWebSocket]);
};
