import { LoginForm } from '../components/LoginForm';
import { LoginCredentials } from '../types';
import { withBasePath } from '../utils/basePath';

interface LoginPageProps {
  onLogin: (credentials: LoginCredentials) => void;
  error: string | null;
  isLoading: boolean;
  authMode?: string;
  oidcButtonLabel?: string | null;
  hideLocalAuth?: boolean;
  oidcAutoRedirect?: boolean;
}

export const LoginPage = ({ onLogin, error, isLoading, authMode, oidcButtonLabel, hideLocalAuth, oidcAutoRedirect }: LoginPageProps) => {
  const logoUrl = withBasePath('/logo.png');

  return (
    <div
      className="min-h-screen flex items-center justify-center px-4 py-8"
      style={{ backgroundColor: 'var(--background-color)', color: 'var(--text-color)' }}
    >
      <div className="w-full max-w-md">
        <div
          className="rounded-lg shadow-2xl p-6 border"
          style={{
            backgroundColor: 'var(--card-background)',
            borderColor: 'var(--border-color)',
            color: 'var(--text-color)',
          }}
        >
          <div className="text-center mb-5">
            <img src={logoUrl} alt="Logo" className="mx-auto w-12 h-12" />
          </div>
          <LoginForm onSubmit={onLogin} error={error} isLoading={isLoading} authMode={authMode} oidcButtonLabel={oidcButtonLabel} hideLocalAuth={hideLocalAuth} oidcAutoRedirect={oidcAutoRedirect} />
        </div>
      </div>
    </div>
  );
};

