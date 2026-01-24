import React, { Fragment } from 'react';
import './App.css';
import MainRoute from '@/routes/index.jsx';
import { HashRouter, Route, Routes } from 'react-router-dom';

const App = () => {
  return (
      <HashRouter>
        {/* <Suspense fallback={<Loading />}> */}
        <Routes>
          {MainRoute.map((route, index) => {
            const Layout = route.Layout === null ? Fragment : route.Layout;
            const Page = route.component;
            return (
              <Route
                key={index}
                path={route.path}
                element={
                  <Layout>
                    <Page />
                  </Layout>
                }
              />
            );
          })}
          <Route
            path="*"
            element={
              <Fragment>
                <h1> 404 Not Found</h1>
              </Fragment>
            }
          />
        </Routes>
        {/* </Suspense> */}

      </HashRouter>
  );
}

export default App;
