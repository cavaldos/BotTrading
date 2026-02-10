import { lazy } from 'react';

const MainLayout = lazy(() => import('@/components/Layout/main.layout'));
const ViewBacktestPage = lazy(() => import('@/pages/ViewBacktest'));
const LiveTradePage = lazy(() => import('@/pages/LiveTrade'));
const IndicatorPage = lazy(() => import('@/pages/Indicator'))
const CombinePage = lazy(() => import('@/pages/Combine'));
const Router = [
    {
        name: 'View Backtest',
        path: '/',
        component: ViewBacktestPage,
        Layout: MainLayout,
    },
    {
        name: 'Live Trade',
        path: '/live-trade',
        component: LiveTradePage,
        Layout: MainLayout,
    },
    {
        name: 'Indicators',
        path: '/indicator',
        component: IndicatorPage,
        Layout: MainLayout,
    },
    {
        name: 'Combine',
        path: '/combine',
        component: CombinePage,
        Layout: MainLayout,
    },

];

export default Router;