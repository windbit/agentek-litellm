import { Alert, Button, Input, Modal, Spin } from "antd";
import { useEffect, useState } from "react";
import { subscriptionsApi, type DeviceLogin, type LoginTarget } from "../api";

const POLL_INTERVAL_MS = 5_000;
const NAME_PATTERN = /^[a-z0-9][a-z0-9-]{0,62}$/;

export interface LoginRequest {
  provider: string;
  /** Set when an existing subscription is reauthorized; undefined adds a new one. */
  subscription?: { id: string; name: string };
}

interface LoginModalProps {
  accessToken: string | null;
  request: LoginRequest | null;
  onClose: () => void;
  onDone: () => void;
}

export default function LoginModal({ accessToken, request, onClose, onDone }: LoginModalProps) {
  const title = request?.subscription ? `Reauthorize ${request.subscription.name}` : "Add a subscription";
  return (
    <Modal open={request !== null} title={title} onCancel={onClose} footer={null} destroyOnHidden>
      {request && <LoginForm accessToken={accessToken} request={request} onDone={onDone} />}
    </Modal>
  );
}

interface LoginFormProps {
  accessToken: string | null;
  request: LoginRequest;
  onDone: () => void;
}

function LoginForm({ accessToken, request, onDone }: LoginFormProps) {
  const [name, setName] = useState("");
  const [login, setLogin] = useState<DeviceLogin | null>(null);
  const [error, setError] = useState<string | null>(null);

  const target: LoginTarget | null = request.subscription
    ? { subscription_id: request.subscription.id }
    : NAME_PATTERN.test(name)
      ? { name }
      : null;

  const start = async () => {
    if (!accessToken) {
      return;
    }
    try {
      setLogin(await subscriptionsApi.loginStart(accessToken, request.provider));
      setError(null);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Could not start the login");
    }
  };

  useEffect(() => {
    if (!login || !target || !accessToken) {
      return;
    }
    const timer = setInterval(async () => {
      try {
        const result = await subscriptionsApi.loginPoll(accessToken, request.provider, login, target);
        if (result.status === "done") {
          onDone();
        }
      } catch (failure) {
        setError(failure instanceof Error ? failure.message : "The login failed");
        setLogin(null);
      }
    }, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [login, accessToken]);

  return (
    <div>
      {!request.subscription && (
        <Input
          placeholder="Name, e.g. chatgpt-team-a"
          value={name}
          disabled={login !== null}
          onChange={(event) => setName(event.target.value.trim())}
          status={name && !target ? "error" : undefined}
        />
      )}
      {login ? (
        <div className="mt-4">
          <p>
            Open{" "}
            <a href={login.verify_url} target="_blank" rel="noreferrer">
              {login.verify_url}
            </a>{" "}
            and enter the code:
          </p>
          <p className="text-2xl font-mono my-2">{login.user_code}</p>
          <Spin size="small" /> <span className="text-gray-500">Waiting for the provider to confirm</span>
        </div>
      ) : (
        <Button className="mt-4" type="primary" disabled={!target} onClick={start}>
          Start login
        </Button>
      )}
      {error && <Alert className="mt-4" type="error" message={error} />}
    </div>
  );
}
