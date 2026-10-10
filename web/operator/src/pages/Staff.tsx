// «Сотрудники» (только supervisor): список операторов и заведение нового с ролью и офисом.
// Бэкенд ходит в Keycloak Admin API сервисным клиентом operator-admin — пароли и токены
// администратора в браузер не попадают. Новый сотрудник сразу может войти.
import { useCallback, useEffect, useState } from "react";
import { api, errText, type StaffUser } from "../api";
import { Empty, ErrorBox, Skeleton, useToast } from "../ui";

const ROLE_RU: Record<string, string> = {
  "doc-reader": "просмотр",
  "doc-editor": "редактор",
  supervisor: "руководитель",
};
const OFFICE_RU: Record<string, string> = {
  moscow: "Москва",
  spb: "Санкт-Петербург",
  "": "все офисы",
};

export function StaffPage() {
  const toast = useToast();
  const [users, setUsers] = useState<StaffUser[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({
    username: "",
    first_name: "",
    last_name: "",
    email: "",
    role: "doc-editor",
    office: "moscow",
    password: "",
  });
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api
      .get<{ items: StaffUser[] } | StaffUser[]>("/api/operator/staff")
      .then((r) => {
        setUsers(Array.isArray(r) ? r : (r.items ?? []));
        setError(null);
      })
      .catch((e) => setError(errText(e)));
  }, []);
  useEffect(load, [load]);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (form.password.length < 12) {
      toast("bad", "Пароль — не короче 12 символов (политика входа)");
      return;
    }
    setBusy(true);
    try {
      await api.post("/api/operator/staff", form);
      toast("ok", `Сотрудник ${form.username} заведён — может входить`);
      setForm({
        ...form,
        username: "",
        first_name: "",
        last_name: "",
        email: "",
        password: "",
      });
      load();
    } catch (err) {
      toast("bad", errText(err));
    } finally {
      setBusy(false);
    }
  }

  const set =
    (k: string) =>
    (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
      setForm({ ...form, [k]: e.target.value });

  return (
    <section>
      <div className="page-head">
        <h1>Сотрудники</h1>
      </div>
      <form className="staff-form" onSubmit={create}>
        <div className="block-title">Новый сотрудник</div>
        <div className="form-grid">
          <input
            className="input"
            placeholder="Логин"
            value={form.username}
            onChange={set("username")}
            required
          />
          <input
            className="input"
            placeholder="Имя"
            value={form.first_name}
            onChange={set("first_name")}
            required
          />
          <input
            className="input"
            placeholder="Фамилия"
            value={form.last_name}
            onChange={set("last_name")}
            required
          />
          <input
            className="input"
            type="email"
            placeholder="Email"
            value={form.email}
            onChange={set("email")}
            required
          />
          <select
            className="input"
            value={form.role}
            onChange={set("role")}
            aria-label="Роль"
          >
            <option value="doc-reader">Просмотр</option>
            <option value="doc-editor">Редактор</option>
            <option value="supervisor">Руководитель</option>
          </select>
          <select
            className="input"
            value={form.office}
            onChange={set("office")}
            aria-label="Офис"
          >
            <option value="moscow">Москва</option>
            <option value="spb">Санкт-Петербург</option>
            <option value="">Без офиса (руководитель)</option>
          </select>
          <input
            className="input"
            type="password"
            placeholder="Пароль (от 12 символов)"
            value={form.password}
            onChange={set("password")}
            required
          />
          <button className="btn primary" disabled={busy}>
            Завести
          </button>
        </div>
      </form>
      {error && <ErrorBox text={error} onRetry={load} />}
      {!users && !error && <Skeleton lines={4} height={32} />}
      {users && users.length === 0 && <Empty title="Сотрудников нет" />}
      {users && users.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Логин</th>
              <th>ФИО</th>
              <th>Роль</th>
              <th>Офис</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.username}>
                <td className="mono">{u.username}</td>
                <td>
                  {u.name ??
                    [u.first_name, u.last_name].filter(Boolean).join(" ")}
                </td>
                <td>
                  {(u.roles ?? []).map((r) => ROLE_RU[r] ?? r).join(", ") ||
                    "—"}
                </td>
                <td>{OFFICE_RU[u.office ?? ""] ?? u.office}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
