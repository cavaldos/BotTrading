import React from 'react';
import Router from '@/routes';
import { useNavigate } from 'react-router-dom';


const Navigation = () => {
    const navigate = useNavigate();

    const handleNavigation = (path) => {
        navigate(path);
    };

    return (
        <nav className="bg-gray-900 shadow-lg">
            <div className="max-w-7xl mx-auto px-4">
                <div className="flex items-center justify-center h-10 gap-2">
                    {Router.filter(route => !route.hidden).map((route, index) => (
                        <button
                            key={index}
                            onClick={() => handleNavigation(route.path)}
                            className="px-4 py-2 text-white font-medium rounded-md transition duration-200 hover:bg-gray-700 active:bg-gray-800"
                        >
                            {route.name}
                        </button>
                    ))}
                </div>
            </div>
        </nav>
    );
}

const MainLayout = ({ children }) => {
    return (
        <div className="main-layout">
            <Navigation />
            <main>{children}</main>
        </div>
    );
}
export default MainLayout;