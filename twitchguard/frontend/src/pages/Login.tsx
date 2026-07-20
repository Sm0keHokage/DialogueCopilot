export default function Login() {
  return (
    <div className="login-screen">
      <div className="login-card">
        <h1>🛡 TwitchGuard</h1>
        <p className="muted">
          ИИ помечает подозрительные сообщения чата — решение всегда принимает живой модератор.
        </p>
        <a className="btn twitch" href="/auth/twitch/login">
          Войти через Twitch
        </a>
        <p className="muted small">
          Логин и пароль вводятся только на сайте Twitch. TwitchGuard никогда не запрашивает
          пароль или коды двухфакторной аутентификации.
        </p>
      </div>
    </div>
  )
}
