import { createRouter, createWebHistory } from 'vue-router'
import Editor from '@/views/Editor/index.vue'
import { PLATFORM_TOKEN_KEY } from '@/services'

const routes = [
  {
    path: '/',
    redirect: () => localStorage.getItem(PLATFORM_TOKEN_KEY) ? '/dashboard' : '/login'
  },
  {
    path: '/login',
    name: 'Auth',
    component: () => import('@/views/Auth/index.vue'),
    meta: { guestOnly: true }
  },
  {
    path: '/dashboard',
    name: 'Dashboard',
    component: () => import('@/views/Dashboard/index.vue'),
    meta: { requiresAuth: true }
  },
  {
    path: '/create',
    name: 'Outline',
    component: () => import('@/views/Outline/index.vue'),
    meta: { requiresAuth: true }
  },
  {
    path: '/editor',
    name: 'Editor',
    component: Editor,
    meta: { requiresAuth: true }
  },
  {
    path: '/ppt',
    name: 'PPT',
    component: () => import('@/views/PPT/index.vue'),
    meta: { requiresAuth: true }
  },
  {
    path: '/app/:id?',
    name: 'APP',
    component: () => import('@/views/APP/index.vue'),
    meta: { requiresAuth: true }
  },
  {
    path: '/:pathMatch(.*)*',
    redirect: '/'
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

router.beforeEach((to) => {
  const authenticated = Boolean(localStorage.getItem(PLATFORM_TOKEN_KEY))
  if (to.meta.requiresAuth && !authenticated) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  if (to.meta.guestOnly && authenticated) return '/dashboard'
})

export default router
