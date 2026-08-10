import { api } from './client'

export type UserRole = 'user' | 'account_admin'
export type UserStatus = 'active' | 'disabled'

export interface UserInfo {
  id: number
  username: string
  role: UserRole
  status: UserStatus
  created_at: string | null
}

export interface CreateUserInput {
  username: string
  password: string
  role?: UserRole
}

export interface UpdateUserInput {
  status?: UserStatus
  password?: string
  role?: UserRole
}

export async function listUsers(): Promise<UserInfo[]> {
  const resp = await api.get<UserInfo[]>('/users')
  return resp.data
}

export async function createUser(input: CreateUserInput): Promise<UserInfo> {
  const resp = await api.post<UserInfo>('/users', input)
  return resp.data
}

export async function updateUser(id: number, input: UpdateUserInput): Promise<UserInfo> {
  const resp = await api.put<UserInfo>(`/users/${id}`, input)
  return resp.data
}
