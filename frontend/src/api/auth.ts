import { request } from './client'

export interface User {
  id: number
  username: string
  display_name: string
  role: 'astronaut' | 'doctor'
  astronaut_id: number | null
  is_test_account: boolean
}

export interface AuthState {
  signed_in: boolean
  user: User | null
  accounts_exist: boolean
  setup_allowed: boolean
  test_accounts: boolean
  test_accounts_file: string | null
  mission_now: string
  test_clock: boolean
}

export const authApi = {
  state: () => request<AuthState>('GET', '/auth/state'),
  login: (username: string, password: string) =>
    request<User>('POST', '/auth/login', { username, password, device_name: 'AM main app' }),
  logout: () => request<{ signed_in: boolean }>('POST', '/auth/logout'),
}
