import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import Home from '../views/Home.vue'
import Editor from '../views/Editor.vue'

const routes: RouteRecordRaw[] = [
  { path: '/', name: 'home', component: Home },
  { path: '/editor/:imageId', name: 'editor', component: Editor, props: true },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})
