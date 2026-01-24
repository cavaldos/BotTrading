import { lazy } from 'react';

const MainLayout = lazy(() => import('@/components/Layout/main.layout'));
const ViewBacktestPage = lazy(() => import('@/pages/ViewBacktest'));
const LiveTradePage = lazy(() => import('@/pages/LiveTrade'));

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


];

export default Router;