var gulp = require('gulp'),
    sass = require('gulp-sass'),
    jshint= require('gulp-jshint'),
    path = require('path'),
    rename = require('gulp-rename'),
    dummy = 'last';


// fix Promise() error from which package again?
require('es6-promise').polyfill();


sass_paths = [
    'djangocms_misc/autopublisher/static/autopublisher/sass/*.sass',
    'djangocms_misc/admin_style/static/djangocms_misc/admin/sass/*.sass',
    'djangocms_misc/static/djangocms_misc/sass/*.sass'
]


gulp.task('sass', function () {
    return gulp.src(sass_paths, { base: '.' })
        .pipe(sass({errLogToConsole: true}))
        .pipe(rename(function(path) {
            path.dirname = path.dirname.replace('/sass', '/css');
            path.extname = '.css';
        }))
        .pipe(gulp.dest('.'));
});


gulp.task('jshint', function () {
    gulp.src(['gulpfile.js', 'djangocms_misc/**.js'])
        .pipe(jshint());
});


gulp.task('default', ['sass', 'jshint']);


gulp.task('watch', function () {
    gulp.watch('djangocms_misc/**/**.sass', ['sass']);
    gulp.watch(['gulpfile.js', 'djangocms_misc/**.js'], ['jshint']);
});

