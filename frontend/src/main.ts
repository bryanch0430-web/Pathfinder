import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import '@fontsource-variable/inter'
import './styles/main.css'

createApp(App).use(createPinia()).mount('#app')
